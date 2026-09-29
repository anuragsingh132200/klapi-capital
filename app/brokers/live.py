from __future__ import annotations

from typing import Any

import httpx

from app.brokers.base import (
    BrokerAdapter,
    BrokerAuthenticationError,
    BrokerError,
    BrokerRateLimitError,
    BrokerTransientError,
    BrokerUncertainOrderError,
)
from app.domain import BrokerOrderResult, Holding, OrderStatus, PlannedOrder, Side


def _status(value: str | None) -> OrderStatus:
    normalized = (value or "").upper().replace(" ", "_")
    if normalized in {"COMPLETE", "COMPLETED", "TRADED", "FILLED", "EXECUTED"}:
        return OrderStatus.COMPLETE
    if normalized in {"REJECTED", "FAILED", "CANCELLED", "CANCELED"}:
        return OrderStatus.REJECTED
    if normalized in {"OPEN", "PENDING", "PLACED", "TRANSIT", "PARTIALLY_FILLED"}:
        return OrderStatus.ACCEPTED
    return OrderStatus.UNKNOWN


class HttpBrokerAdapter(BrokerAdapter):
    base_url = ""

    def __init__(self, credentials: dict[str, Any], timeout: float = 10.0) -> None:
        super().__init__(credentials, timeout)
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)
        self._live_enabled = bool(credentials.get("_live_trading_enabled", False))

    async def _request(
        self,
        method: str,
        path: str,
        *,
        order_sensitive: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            error = BrokerUncertainOrderError if order_sensitive else BrokerTransientError
            raise error(f"Broker request timed out: {exc}") from exc
        except httpx.TransportError as exc:
            error = BrokerUncertainOrderError if order_sensitive else BrokerTransientError
            raise error(f"Broker transport error: {exc}") from exc

        if response.status_code in {401, 403}:
            raise BrokerAuthenticationError("Broker rejected the supplied credentials")
        if response.status_code == 429:
            raise BrokerRateLimitError("Broker rate limit exceeded")
        if response.status_code >= 500:
            error = BrokerUncertainOrderError if order_sensitive else BrokerTransientError
            raise error(f"Broker unavailable ({response.status_code})")
        if response.status_code >= 400:
            detail = response.text[:500]
            raise BrokerError(f"Broker request failed ({response.status_code}): {detail}")
        try:
            data = response.json()
        except ValueError as exc:
            raise BrokerError("Broker returned a non-JSON response") from exc
        if not isinstance(data, dict):
            raise BrokerError("Broker returned an unexpected response")
        return data

    def _assert_live_enabled(self) -> None:
        if not self._live_enabled:
            raise BrokerError(
                "Live trading is disabled. Set ENABLE_LIVE_TRADING=true only after "
                "sandbox validation and an explicit deployment review."
            )

    async def close(self) -> None:
        await self._client.aclose()


class ZerodhaAdapter(HttpBrokerAdapter):
    base_url = "https://api.kite.trade"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": (
                f"token {self.credentials['api_key']}:{self.credentials['access_token']}"
            ),
            "X-Kite-Version": "3",
        }

    async def validate_credentials(self) -> None:
        await self._request("GET", "/user/profile", headers=self.headers)

    async def get_holdings(self) -> list[Holding]:
        data = await self._request("GET", "/portfolio/holdings", headers=self.headers)
        return [
            Holding(
                symbol=item["tradingsymbol"],
                exchange=item.get("exchange", "NSE"),
                quantity=max(0, int(item.get("quantity", 0))),
            )
            for item in data.get("data", [])
        ]

    async def place_order(self, order: PlannedOrder) -> BrokerOrderResult:
        self._assert_live_enabled()
        data = await self._request(
            "POST",
            "/orders/regular",
            headers=self.headers,
            data={
                "tradingsymbol": order.symbol,
                "exchange": order.exchange,
                "transaction_type": order.side.value,
                "order_type": "MARKET",
                "quantity": order.quantity,
                "product": "CNC",
                "validity": "DAY",
                "tag": (order.client_order_id or "")[:20],
            },
            order_sensitive=True,
        )
        order_id = str(data.get("data", {}).get("order_id", "")) or None
        return BrokerOrderResult(broker_order_id=order_id, status=OrderStatus.ACCEPTED, raw=data)

    async def get_order_status(self, order_id: str) -> BrokerOrderResult:
        data = await self._request("GET", f"/orders/{order_id}", headers=self.headers)
        history = data.get("data", [])
        latest = history[-1] if history else {}
        return BrokerOrderResult(
            broker_order_id=order_id,
            status=_status(latest.get("status")),
            message=latest.get("status_message"),
            raw=data,
        )


class FyersAdapter(HttpBrokerAdapter):
    base_url = "https://api-t1.fyers.in/api/v3"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"{self.credentials['client_id']}:{self.credentials['access_token']}",
            "Content-Type": "application/json",
        }

    async def validate_credentials(self) -> None:
        await self._request("GET", "/profile", headers=self.headers)

    async def get_holdings(self) -> list[Holding]:
        data = await self._request("GET", "/holdings", headers=self.headers)
        return [
            Holding(
                symbol=str(item.get("symbol", "")).split(":")[-1].removesuffix("-EQ"),
                exchange=str(item.get("symbol", "NSE:")).split(":")[0],
                quantity=max(0, int(item.get("quantity", item.get("remainingQuantity", 0)))),
            )
            for item in data.get("holdings", [])
        ]

    async def place_order(self, order: PlannedOrder) -> BrokerOrderResult:
        self._assert_live_enabled()
        data = await self._request(
            "POST",
            "/orders/sync",
            headers=self.headers,
            json={
                "symbol": f"{order.exchange}:{order.symbol}-EQ",
                "qty": order.quantity,
                "type": 2,
                "side": 1 if order.side == Side.BUY else -1,
                "productType": "CNC",
                "limitPrice": 0,
                "stopPrice": 0,
                "validity": "DAY",
                "disclosedQty": 0,
                "offlineOrder": False,
                "orderTag": order.client_order_id,
            },
            order_sensitive=True,
        )
        order_id = str(data.get("id", "")) or None
        return BrokerOrderResult(
            broker_order_id=order_id,
            status=OrderStatus.ACCEPTED if data.get("s") == "ok" else OrderStatus.REJECTED,
            message=data.get("message"),
            raw=data,
        )

    async def get_order_status(self, order_id: str) -> BrokerOrderResult:
        data = await self._request(
            "GET", "/orders", headers=self.headers, params={"id": order_id}
        )
        orders = data.get("orderBook", [])
        item = orders[0] if orders else {}
        # FYERS uses numeric status codes; 2 is filled, 5 is rejected, 6 is cancelled.
        raw_status = item.get("status")
        status = (
            OrderStatus.COMPLETE
            if raw_status == 2
            else OrderStatus.REJECTED
            if raw_status in {5, 6}
            else OrderStatus.ACCEPTED
        )
        return BrokerOrderResult(broker_order_id=order_id, status=status, raw=data)


class AngelOneAdapter(HttpBrokerAdapter):
    base_url = "https://apiconnect.angelone.in"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.credentials['access_token']}",
            "X-PrivateKey": str(self.credentials["api_key"]),
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": str(self.credentials.get("client_local_ip", "127.0.0.1")),
            "X-ClientPublicIP": str(self.credentials.get("client_public_ip", "127.0.0.1")),
            "X-MACAddress": str(self.credentials.get("mac_address", "00:00:00:00:00:00")),
            "Content-Type": "application/json",
        }

    async def validate_credentials(self) -> None:
        await self._request(
            "GET",
            "/rest/secure/angelbroking/portfolio/v1/getHolding",
            headers=self.headers,
        )

    async def get_holdings(self) -> list[Holding]:
        data = await self._request(
            "GET",
            "/rest/secure/angelbroking/portfolio/v1/getHolding",
            headers=self.headers,
        )
        return [
            Holding(
                symbol=str(item.get("tradingsymbol", "")).removesuffix("-EQ"),
                exchange=item.get("exchange", "NSE"),
                quantity=max(0, int(float(item.get("quantity", 0)))),
            )
            for item in (data.get("data") or [])
        ]

    def _symbol_token(self, order: PlannedOrder) -> str:
        tokens = self.credentials.get("instrument_tokens", {})
        token = tokens.get(f"{order.exchange}:{order.symbol}") or tokens.get(order.symbol)
        if not token:
            raise BrokerError(
                f"Angel One instrument token missing for {order.exchange}:{order.symbol}"
            )
        return str(token)

    async def place_order(self, order: PlannedOrder) -> BrokerOrderResult:
        self._assert_live_enabled()
        data = await self._request(
            "POST",
            "/rest/secure/angelbroking/order/v1/placeOrder",
            headers=self.headers,
            json={
                "variety": "NORMAL",
                "tradingsymbol": f"{order.symbol}-EQ",
                "symboltoken": self._symbol_token(order),
                "transactiontype": order.side.value,
                "exchange": order.exchange,
                "ordertype": "MARKET",
                "producttype": "DELIVERY",
                "duration": "DAY",
                "price": "0",
                "squareoff": "0",
                "stoploss": "0",
                "quantity": str(order.quantity),
            },
            order_sensitive=True,
        )
        payload = data.get("data") or {}
        order_id = str(payload.get("orderid", "")) or None
        return BrokerOrderResult(
            broker_order_id=order_id,
            status=OrderStatus.ACCEPTED if data.get("status") else OrderStatus.REJECTED,
            message=data.get("message"),
            raw=data,
        )

    async def get_order_status(self, order_id: str) -> BrokerOrderResult:
        data = await self._request(
            "GET", "/rest/secure/angelbroking/order/v1/getOrderBook", headers=self.headers
        )
        orders = data.get("data") or []
        item = next((entry for entry in orders if str(entry.get("orderid")) == order_id), {})
        return BrokerOrderResult(
            broker_order_id=order_id,
            status=_status(item.get("orderstatus") or item.get("status")),
            message=item.get("text"),
            raw=data,
        )


class GrowwAdapter(HttpBrokerAdapter):
    base_url = "https://api.groww.in"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.credentials['access_token']}",
            "X-API-VERSION": "1.0",
            "Accept": "application/json",
        }

    async def validate_credentials(self) -> None:
        await self._request("GET", "/v1/holdings/user", headers=self.headers)

    async def get_holdings(self) -> list[Holding]:
        data = await self._request("GET", "/v1/holdings/user", headers=self.headers)
        items = (data.get("payload") or {}).get("holdings", [])
        return [
            Holding(
                symbol=item["trading_symbol"],
                exchange=item.get("exchange", "NSE"),
                quantity=max(0, int(float(item.get("quantity", 0)))),
            )
            for item in items
        ]

    async def place_order(self, order: PlannedOrder) -> BrokerOrderResult:
        self._assert_live_enabled()
        data = await self._request(
            "POST",
            "/v1/order/create",
            headers={**self.headers, "Content-Type": "application/json"},
            json={
                "trading_symbol": order.symbol,
                "quantity": order.quantity,
                "price": 0,
                "trigger_price": 0,
                "validity": "DAY",
                "exchange": order.exchange,
                "segment": "CASH",
                "product": "CNC",
                "order_type": "MARKET",
                "transaction_type": order.side.value,
                "order_reference_id": (order.client_order_id or "kalpiorder")[:20],
            },
            order_sensitive=True,
        )
        payload = data.get("payload") or {}
        return BrokerOrderResult(
            broker_order_id=payload.get("groww_order_id"),
            status=_status(payload.get("order_status")),
            message=payload.get("remark"),
            raw=data,
        )

    async def get_order_status(self, order_id: str) -> BrokerOrderResult:
        data = await self._request(
            "GET",
            f"/v1/order/status/{order_id}",
            headers=self.headers,
            params={"segment": "CASH"},
        )
        payload = data.get("payload") or {}
        return BrokerOrderResult(
            broker_order_id=order_id,
            status=_status(payload.get("order_status")),
            message=payload.get("remark"),
            raw=data,
        )


class UpstoxAdapter(HttpBrokerAdapter):
    base_url = "https://api.upstox.com"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.credentials['access_token']}",
            "Accept": "application/json",
        }

    async def validate_credentials(self) -> None:
        await self._request("GET", "/v2/user/profile", headers=self.headers)

    async def get_holdings(self) -> list[Holding]:
        data = await self._request(
            "GET", "/v2/portfolio/long-term-holdings", headers=self.headers
        )
        return [
            Holding(
                symbol=item["tradingsymbol"],
                exchange=item.get("exchange", "NSE"),
                quantity=max(0, int(float(item.get("quantity", 0)))),
            )
            for item in data.get("data", [])
        ]

    def _instrument_key(self, order: PlannedOrder) -> str:
        instruments = self.credentials.get("instrument_keys", {})
        value = instruments.get(f"{order.exchange}:{order.symbol}") or instruments.get(
            order.symbol
        )
        if not value:
            raise BrokerError(
                f"Upstox instrument key missing for {order.exchange}:{order.symbol}"
            )
        return str(value)

    async def place_order(self, order: PlannedOrder) -> BrokerOrderResult:
        self._assert_live_enabled()
        data = await self._request(
            "POST",
            "https://api-hft.upstox.com/v3/order/place",
            headers={**self.headers, "Content-Type": "application/json"},
            json={
                "quantity": order.quantity,
                "product": "D",
                "validity": "DAY",
                "price": 0,
                "tag": (order.client_order_id or "")[:40],
                "instrument_token": self._instrument_key(order),
                "order_type": "MARKET",
                "transaction_type": order.side.value,
                "disclosed_quantity": 0,
                "trigger_price": 0,
                "is_amo": False,
                "slice": False,
            },
            order_sensitive=True,
        )
        order_ids = (data.get("data") or {}).get("order_ids") or []
        order_id = (data.get("data") or {}).get("order_id") or (order_ids[0] if order_ids else None)
        return BrokerOrderResult(
            broker_order_id=order_id,
            status=OrderStatus.ACCEPTED,
            raw=data,
        )

    async def get_order_status(self, order_id: str) -> BrokerOrderResult:
        data = await self._request(
            "GET",
            "/v2/order/details",
            headers=self.headers,
            params={"order_id": order_id},
        )
        payload = data.get("data") or {}
        return BrokerOrderResult(
            broker_order_id=order_id,
            status=_status(payload.get("status")),
            message=payload.get("status_message"),
            raw=data,
        )
