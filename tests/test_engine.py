import pytest

from app.brokers.base import BrokerAdapter
from app.database import Database
from app.domain import (
    BrokerOrderResult,
    ExecutionRequest,
    ExecutionStatus,
    Holding,
    OrderStatus,
    PlannedOrder,
)
from app.engine import ExecutionEngine


class StubNotifier:
    def __init__(self):
        self.results = []

    async def send(self, result):
        self.results.append(result)


class StubFactory:
    def __init__(self, broker):
        self.broker = broker

    def get(self, broker_name):
        return self.broker


class PendingThenCompleteBroker(BrokerAdapter):
    def __init__(self, *, stays_pending=False):
        super().__init__({})
        self.status_calls = 0
        self.place_calls = 0
        self.stays_pending = stays_pending

    async def validate_credentials(self):
        return None

    async def get_holdings(self) -> list[Holding]:
        return []

    async def place_order(self, order: PlannedOrder) -> BrokerOrderResult:
        self.place_calls += 1
        return BrokerOrderResult(
            broker_order_id="broker-order-1", status=OrderStatus.ACCEPTED
        )

    async def get_order_status(self, order_id: str) -> BrokerOrderResult:
        self.status_calls += 1
        status = (
            OrderStatus.ACCEPTED
            if self.stays_pending or self.status_calls == 1
            else OrderStatus.COMPLETE
        )
        return BrokerOrderResult(broker_order_id=order_id, status=status)


def request() -> ExecutionRequest:
    return ExecutionRequest.model_validate(
        {
            "broker": "mock",
            "mode": "FIRST_TIME",
            "target_portfolio": [{"symbol": "INFY", "quantity": 1}],
        }
    )


@pytest.mark.asyncio
async def test_engine_waits_for_terminal_order_status(tmp_path):
    database = Database(str(tmp_path / "terminal.db"))
    database.initialize()
    broker = PendingThenCompleteBroker()
    notifier = StubNotifier()
    engine = ExecutionEngine(
        database,
        StubFactory(broker),
        notifier,
        poll_interval_seconds=0.001,
        poll_timeout_seconds=0.1,
    )

    result = await engine.execute(request(), "terminal-status-1")

    assert result.status == ExecutionStatus.COMPLETED
    assert result.orders[0].status == OrderStatus.COMPLETE
    assert broker.status_calls == 2
    assert len(notifier.results) == 1
    database.close()


@pytest.mark.asyncio
async def test_non_terminal_order_is_not_reported_complete_or_retried(tmp_path):
    database = Database(str(tmp_path / "timeout.db"))
    database.initialize()
    broker = PendingThenCompleteBroker(stays_pending=True)
    engine = ExecutionEngine(
        database,
        StubFactory(broker),
        StubNotifier(),
        poll_interval_seconds=0.001,
        poll_timeout_seconds=0.005,
    )

    result = await engine.execute(request(), "terminal-timeout-1")

    assert result.status == ExecutionStatus.FAILED
    assert result.orders[0].status == OrderStatus.UNKNOWN
    assert "reconcile" in result.orders[0].message
    assert broker.place_calls == 1
    database.close()
