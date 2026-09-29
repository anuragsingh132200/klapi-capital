from __future__ import annotations

import asyncio
import uuid

from app.brokers.base import (
    BrokerError,
    BrokerRateLimitError,
    BrokerTransientError,
)
from app.brokers.factory import BrokerFactory
from app.database import Database
from app.domain import (
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    OrderResult,
    OrderStatus,
    PlannedOrder,
    utc_now,
)
from app.notifications import NotificationService
from app.planner import plan_orders


class DuplicateExecutionInProgress(RuntimeError):
    pass


class ExecutionEngine:
    def __init__(
        self,
        database: Database,
        broker_factory: BrokerFactory,
        notifier: NotificationService,
        max_attempts: int = 3,
    ) -> None:
        self.database = database
        self.broker_factory = broker_factory
        self.notifier = notifier
        self.max_attempts = max(1, max_attempts)

    async def execute(self, request: ExecutionRequest, idempotency_key: str) -> ExecutionResult:
        previous = self.database.get_by_idempotency_key(idempotency_key)
        if previous:
            return previous

        execution_id = f"exec_{uuid.uuid4().hex}"
        created = self.database.create_execution(
            execution_id=execution_id,
            idempotency_key=idempotency_key,
            broker=request.broker.value,
            mode=request.mode.value,
            request=request.model_dump(mode="json"),
        )
        if not created:
            previous = self.database.get_by_idempotency_key(idempotency_key)
            if previous:
                return previous
            raise DuplicateExecutionInProgress("This idempotent execution is still in progress")

        try:
            broker = self.broker_factory.get(request.broker)
            holdings = await broker.get_holdings()
            plan = plan_orders(request, holdings)
        except Exception:
            # No order has been attempted, so it is safe to release the reservation and
            # allow the caller to correct validation/authentication data using the same key.
            self.database.delete_pending_execution(execution_id)
            raise
        for order in plan:
            # Stable broker-visible key supports later reconciliation after uncertain outcomes.
            order.client_order_id = f"{execution_id[-10:]}-{order.sequence}"

        if not plan:
            result = ExecutionResult(
                execution_id=execution_id,
                broker=request.broker,
                mode=request.mode,
                status=ExecutionStatus.NO_ACTION,
                orders=[],
                created_at=utc_now(),
                completed_at=utc_now(),
            )
            self.database.save_execution_result(result)
            await self.notifier.send(result)
            return result

        results = [await self._place_with_retry(broker, order) for order in plan]
        successful = sum(
            item.status in {OrderStatus.ACCEPTED, OrderStatus.COMPLETE} for item in results
        )
        if successful == len(results):
            status = ExecutionStatus.COMPLETED
        elif successful:
            status = ExecutionStatus.PARTIALLY_COMPLETED
        else:
            status = ExecutionStatus.FAILED

        result = ExecutionResult(
            execution_id=execution_id,
            broker=request.broker,
            mode=request.mode,
            status=status,
            orders=results,
            created_at=utc_now(),
            completed_at=utc_now(),
        )
        self.database.save_execution_result(result)
        await self.notifier.send(result)
        return result

    async def _place_with_retry(self, broker, order: PlannedOrder) -> OrderResult:
        attempts = 0
        while attempts < self.max_attempts:
            attempts += 1
            try:
                placed = await broker.place_order(order)
                if placed.status == OrderStatus.ACCEPTED and placed.broker_order_id:
                    try:
                        checked = await broker.get_order_status(placed.broker_order_id)
                        if checked.status != OrderStatus.UNKNOWN:
                            placed = checked
                    except BrokerError:
                        # An accepted order remains accepted if status polling fails.
                        pass
                return OrderResult(
                    symbol=order.symbol,
                    exchange=order.exchange,
                    side=order.side,
                    quantity=order.quantity,
                    status=placed.status,
                    broker_order_id=placed.broker_order_id,
                    message=placed.message,
                    attempts=attempts,
                )
            except (BrokerRateLimitError, BrokerTransientError) as exc:
                if attempts >= self.max_attempts:
                    return self._failed(order, attempts, str(exc))
                await asyncio.sleep(min(0.1 * (2 ** (attempts - 1)), 1.0))
            except BrokerError as exc:
                return self._failed(order, attempts, str(exc))
            except Exception as exc:  # keep one malformed order from aborting the batch
                return self._failed(order, attempts, f"Unexpected broker error: {exc}")
        return self._failed(order, attempts, "Order attempts exhausted")

    @staticmethod
    def _failed(order: PlannedOrder, attempts: int, message: str) -> OrderResult:
        return OrderResult(
            symbol=order.symbol,
            exchange=order.exchange,
            side=order.side,
            quantity=order.quantity,
            status=OrderStatus.FAILED,
            message=message,
            attempts=attempts,
        )
