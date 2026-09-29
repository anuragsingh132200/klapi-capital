from __future__ import annotations

import hashlib
import secrets
import time
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from app.brokers.base import (
    BrokerAuthenticationError,
    BrokerRateLimitError,
    BrokerTransientError,
)
from app.database import Database
from app.domain import (
    BrokerAuthStartRequest,
    BrokerAuthStartResponse,
    BrokerName,
    GrowwTokenRequest,
    utc_now,
)
from app.security import CredentialVault


class BrokerAuthService:
    """Broker-hosted login orchestration with single-use CSRF state."""

    def __init__(
        self,
        database: Database,
        vault: CredentialVault,
        *,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.database = database
        self.vault = vault
        self.timeout = timeout
        self._client = client

    def start(
        self, broker: BrokerName, request: BrokerAuthStartRequest
    ) -> BrokerAuthStartResponse:
        if broker == BrokerName.MOCK:
            raise ValueError("Mock broker does not require hosted authentication")
        if broker == BrokerName.GROWW:
            raise ValueError("Groww uses the token endpoint, not a redirect flow")
        if broker in {BrokerName.ZERODHA, BrokerName.FYERS, BrokerName.UPSTOX}:
            if not request.api_secret:
                raise ValueError(f"api_secret is required for {broker.value}")

        state = secrets.token_urlsafe(32)
        expires_at = utc_now() + timedelta(minutes=10)
        config = request.model_dump()
        self.database.save_auth_state(
            state=state,
            broker=broker.value,
            encrypted_config=self.vault.encrypt(config),
            expires_at=expires_at.isoformat(),
        )
        url = self._authorization_url(broker, request, state)
        return BrokerAuthStartResponse(
            broker=broker, authorization_url=url, state=state, expires_at=expires_at
        )

    @staticmethod
    def _authorization_url(
        broker: BrokerName, request: BrokerAuthStartRequest, state: str
    ) -> str:
        if broker == BrokerName.ZERODHA:
            redirect_params = urlencode({"state": state})
            return "https://kite.zerodha.com/connect/login?" + urlencode(
                {"v": "3", "api_key": request.api_key, "redirect_params": redirect_params}
            )
        if broker == BrokerName.FYERS:
            return "https://api-t1.fyers.in/api/v3/generate-authcode?" + urlencode(
                {
                    "client_id": request.api_key,
                    "redirect_uri": request.redirect_uri,
                    "response_type": "code",
                    "state": state,
                }
            )
        if broker == BrokerName.ANGELONE:
            return "https://smartapi.angelone.in/publisher-login?" + urlencode(
                {
                    "api_key": request.api_key,
                    "redirect_url": request.redirect_uri,
                    "state": state,
                }
            )
        if broker == BrokerName.UPSTOX:
            return "https://api.upstox.com/v2/login/authorization/dialog?" + urlencode(
                {
                    "response_type": "code",
                    "client_id": request.api_key,
                    "redirect_uri": request.redirect_uri,
                    "state": state,
                }
            )
        raise ValueError(f"Redirect authentication is unsupported for {broker.value}")

    async def exchange_callback(
        self,
        broker: BrokerName,
        *,
        state: str,
        code: str | None = None,
        request_token: str | None = None,
        auth_token: str | None = None,
        feed_token: str | None = None,
    ) -> dict[str, Any]:
        consumed = self.database.consume_auth_state(state, broker.value)
        if not consumed:
            raise BrokerAuthenticationError("Invalid, consumed, or mismatched OAuth state")
        encrypted, expires_at_raw = consumed
        expires_at = datetime.fromisoformat(expires_at_raw)
        if utc_now() > expires_at:
            raise BrokerAuthenticationError("OAuth state has expired")
        config = self.vault.decrypt(encrypted)

        if broker == BrokerName.ANGELONE:
            if not auth_token:
                raise BrokerAuthenticationError("Angel One callback did not contain auth_token")
            return {
                "api_key": config["api_key"],
                "access_token": auth_token,
                "feed_token": feed_token,
            }
        if broker == BrokerName.ZERODHA:
            token = request_token or code
            if not token:
                raise BrokerAuthenticationError("Zerodha callback did not contain request_token")
            checksum = hashlib.sha256(
                f"{config['api_key']}{token}{config['api_secret']}".encode()
            ).hexdigest()
            data = await self._post(
                "https://api.kite.trade/session/token",
                headers={"X-Kite-Version": "3"},
                data={"api_key": config["api_key"], "request_token": token, "checksum": checksum},
            )
            token = (data.get("data") or {}).get("access_token")
            if not token:
                raise BrokerAuthenticationError(
                    "Zerodha token response did not contain an access token"
                )
            return {"api_key": config["api_key"], "access_token": token}
        if broker == BrokerName.FYERS:
            if not code:
                raise BrokerAuthenticationError("FYERS callback did not contain auth code")
            app_hash = hashlib.sha256(
                f"{config['api_key']}:{config['api_secret']}".encode()
            ).hexdigest()
            data = await self._post(
                "https://api-t1.fyers.in/api/v3/validate-authcode",
                json={"grant_type": "authorization_code", "appIdHash": app_hash, "code": code},
            )
            token = data.get("access_token")
            if not token:
                raise BrokerAuthenticationError(
                    "FYERS token response did not contain an access token"
                )
            return {"client_id": config["api_key"], "access_token": token}
        if broker == BrokerName.UPSTOX:
            if not code:
                raise BrokerAuthenticationError("Upstox callback did not contain code")
            data = await self._post(
                "https://api.upstox.com/v2/login/authorization/token",
                headers={"Accept": "application/json"},
                data={
                    "code": code,
                    "client_id": config["api_key"],
                    "client_secret": config["api_secret"],
                    "redirect_uri": config["redirect_uri"],
                    "grant_type": "authorization_code",
                },
            )
            token = data.get("access_token")
            if not token:
                raise BrokerAuthenticationError(
                    "Upstox token response did not contain an access token"
                )
            return {"access_token": token}
        raise BrokerAuthenticationError(f"No callback exchange for {broker.value}")

    async def generate_groww_token(self, request: GrowwTokenRequest) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {request.api_key}"}
        if request.api_secret:
            timestamp = str(int(time.time()))
            payload = {
                "key_type": "approval",
                "checksum": hashlib.sha256(
                    f"{request.api_secret}{timestamp}".encode()
                ).hexdigest(),
                "timestamp": timestamp,
            }
        else:
            payload = {"key_type": "totp", "totp": request.totp}
        data = await self._post(
            "https://api.groww.in/v1/token/api/access", headers=headers, json=payload
        )
        token = data.get("token") or (data.get("payload") or {}).get("token")
        if not token:
            raise BrokerAuthenticationError("Groww token response did not contain a token")
        return {"access_token": token}

    async def _post(self, url: str, **kwargs: Any) -> dict[str, Any]:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await client.post(url, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise BrokerTransientError("Broker authentication request failed") from exc
        finally:
            if owns_client:
                await client.aclose()
        if response.status_code in {401, 403}:
            raise BrokerAuthenticationError("Broker rejected the authentication exchange")
        if response.status_code == 429:
            raise BrokerRateLimitError("Broker authentication rate limit exceeded")
        if response.status_code >= 400:
            raise BrokerAuthenticationError(
                f"Broker authentication failed ({response.status_code}): {response.text[:300]}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise BrokerAuthenticationError(
                "Broker authentication returned invalid JSON"
            ) from exc
        if not isinstance(data, dict):
            raise BrokerAuthenticationError("Broker authentication returned invalid JSON")
        return data
