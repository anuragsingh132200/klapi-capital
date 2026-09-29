from __future__ import annotations

import uuid
from typing import Any

from app.brokers.base import BrokerAdapter, BrokerRateLimitError
from app.domain import BrokerOrderResult, Holding, OrderStatus, PlannedOrder, Side


class MockBrokerAdapter(BrokerAdapter):
    """Deterministic demo broker.

    Symbols prefixed with FAIL are rejected. Symbols prefixed with RATE are rate-limited
    once and then succeed, allowing retry behavior to be demonstrated.
    """

    def __init__(self, credentials: dict[str, Any], timeout: float = 10.0) -> None:
        super().__init__(credentials, timeout)
        raw_holdings = credentials.get("holdings", [])
        self._holdings: dict[tuple[str, str], int] = {
            (str(item["symbol"]).upper(), str(item.get("exchange", "NSE")).upper()): int(
                item["quantity"]
            )
            for item in raw_holdings
        }
        self._orders: dict[str, BrokerOrderResult] = {}
        self._rate_attempts: dict[str, int] = {}

    async def validate_credentials(self) -> None:
        return None

    async def get_holdings(self) -> list[Holding]:
        return [
            Holding(symbol=symbol, exchange=exchange, quantity=quantity)
            for (symbol, exchange), quantity in sorted(self._holdings.items())
            if quantity > 0
        ]

    async def place_order(self, order: PlannedOrder) -> BrokerOrderResult:
        symbol = order.symbol.upper()
        if symbol.startswith("RATE"):
            attempts = self._rate_attempts.get(symbol, 0)
            self._rate_attempts[symbol] = attempts + 1
            if attempts == 0:
                raise BrokerRateLimitError("Simulated broker rate limit")

        if symbol.startswith("FAIL"):
            return BrokerOrderResult(
                status=OrderStatus.REJECTED,
                message="Simulated broker rejection",
            )

        key = (symbol, order.exchange.upper())
        current = self._holdings.get(key, 0)
        if order.side == Side.SELL and order.quantity > current:
            return BrokerOrderResult(
                status=OrderStatus.REJECTED,
                message=f"Insufficient holding: available={current}",
            )

        order_id = f"mock-{uuid.uuid4().hex[:12]}"
        change = order.quantity if order.side == Side.BUY else -order.quantity
        self._holdings[key] = current + change
        result = BrokerOrderResult(
            broker_order_id=order_id,
            status=OrderStatus.COMPLETE,
            message="Executed by mock broker",
        )
        self._orders[order_id] = result
        return result

    async def get_order_status(self, order_id: str) -> BrokerOrderResult:
        return self._orders.get(
            order_id,
            BrokerOrderResult(status=OrderStatus.UNKNOWN, message="Order not found"),
        )
