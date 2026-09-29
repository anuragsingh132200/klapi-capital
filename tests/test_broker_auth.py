from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.broker_auth import BrokerAuthService
from app.brokers.base import BrokerAuthenticationError
from app.database import Database
from app.domain import BrokerAuthStartRequest, BrokerName, GrowwTokenRequest
from app.security import CredentialVault


def build_service(tmp_path, handler):
    database = Database(str(tmp_path / "auth.db"))
    database.initialize()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = BrokerAuthService(
        database, CredentialVault("auth-test-secret"), client=client
    )
    return database, client, service


@pytest.mark.asyncio
async def test_upstox_authorization_code_flow_uses_and_consumes_state(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.upstox.com/v2/login/authorization/token"
        body = request.content.decode()
        assert "code=single-use-code" in body
        assert "client_secret=app-secret" in body
        return httpx.Response(200, json={"access_token": "issued-access-token"})

    database, client, service = build_service(tmp_path, handler)
    started = service.start(
        BrokerName.UPSTOX,
        BrokerAuthStartRequest(
            api_key="app-key",
            api_secret="app-secret",
            redirect_uri="https://example.test/api/v1/brokers/upstox/auth/callback",
        ),
    )
    query = parse_qs(urlparse(started.authorization_url).query)
    assert query["state"] == [started.state]
    assert query["client_id"] == ["app-key"]

    credentials = await service.exchange_callback(
        BrokerName.UPSTOX, state=started.state, code="single-use-code"
    )
    assert credentials == {"access_token": "issued-access-token"}
    with pytest.raises(BrokerAuthenticationError, match="consumed"):
        await service.exchange_callback(
            BrokerName.UPSTOX, state=started.state, code="single-use-code"
        )
    await client.aclose()
    database.close()


@pytest.mark.asyncio
async def test_groww_totp_token_flow(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer groww-key"
        assert b'"key_type":"totp"' in request.content
        return httpx.Response(200, json={"token": "groww-access-token"})

    database, client, service = build_service(tmp_path, handler)
    credentials = await service.generate_groww_token(
        GrowwTokenRequest(api_key="groww-key", totp="123456")
    )
    assert credentials == {"access_token": "groww-access-token"}
    await client.aclose()
    database.close()


@pytest.mark.asyncio
async def test_zerodha_start_stores_app_secret_encrypted(tmp_path):
    database, client, service = build_service(
        tmp_path, lambda request: httpx.Response(500)
    )
    started = service.start(
        BrokerName.ZERODHA,
        BrokerAuthStartRequest(
            api_key="kite-key",
            api_secret="kite-secret",
            redirect_uri="https://example.test/callback",
        ),
    )
    assert "kite.zerodha.com/connect/login" in started.authorization_url
    row = database.connection.execute(
        "SELECT encrypted_config FROM broker_auth_states WHERE state = ?", (started.state,)
    ).fetchone()
    assert row is not None
    assert "kite-secret" not in row["encrypted_config"]
    await client.aclose()
    database.close()
