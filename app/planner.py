from __future__ import annotations

from collections import Counter

from app.domain import (
    ExecutionMode,
    ExecutionRequest,
    Holding,
    PlannedOrder,
    RebalanceAction,
    Side,
)


class PlanningError(ValueError):
    pass


def _key(symbol: str, exchange: str) -> tuple[str, str]:
    return symbol.strip().upper(), exchange.strip().upper()


def _sequence(orders: list[PlannedOrder]) -> list[PlannedOrder]:
    ordered = sorted(orders, key=lambda order: 0 if order.side == Side.SELL else 1)
    for index, order in enumerate(ordered, start=1):
        order.sequence = index
    return ordered


def plan_orders(request: ExecutionRequest, holdings: list[Holding]) -> list[PlannedOrder]:
    current = {
        _key(item.symbol, item.exchange): item.quantity
        for item in holdings
        if item.quantity > 0
    }
    if request.mode == ExecutionMode.FIRST_TIME:
        return _plan_target(request, current)
    return _plan_rebalance(request, current)


def _plan_target(
    request: ExecutionRequest, current: dict[tuple[str, str], int]
) -> list[PlannedOrder]:
    targets: dict[tuple[str, str], int] = {}
    for item in request.target_portfolio or []:
        key = _key(item.symbol, item.exchange)
        if key in targets:
            raise PlanningError(f"Duplicate target position: {key[1]}:{key[0]}")
        targets[key] = item.quantity

    orders: list[PlannedOrder] = []
    for key in sorted(set(current) | set(targets)):
        symbol, exchange = key
        delta = targets.get(key, 0) - current.get(key, 0)
        if delta:
            orders.append(
                PlannedOrder(
                    symbol=symbol,
                    exchange=exchange,
                    side=Side.BUY if delta > 0 else Side.SELL,
                    quantity=abs(delta),
                )
            )
    return _sequence(orders)


def _plan_rebalance(
    request: ExecutionRequest, current: dict[tuple[str, str], int]
) -> list[PlannedOrder]:
    instructions = request.instructions or []
    seen: set[tuple[str, str, RebalanceAction]] = set()
    sells: Counter[tuple[str, str]] = Counter()
    orders: list[PlannedOrder] = []

    for instruction in instructions:
        symbol, exchange = _key(instruction.symbol, instruction.exchange)
        identity = (symbol, exchange, instruction.action)
        if identity in seen:
            raise PlanningError(
                f"Duplicate instruction: {instruction.action.value} {exchange}:{symbol}"
            )
        seen.add(identity)
        side = (
            Side.SELL
            if instruction.action in {RebalanceAction.SELL, RebalanceAction.ADJUST_SELL}
            else Side.BUY
        )
        if side == Side.SELL:
            sells[(symbol, exchange)] += instruction.quantity
        orders.append(
            PlannedOrder(
                symbol=symbol,
                exchange=exchange,
                side=side,
                quantity=instruction.quantity,
            )
        )

    for key, quantity in sells.items():
        if quantity > current.get(key, 0):
            raise PlanningError(
                f"Cannot sell {quantity} {key[1]}:{key[0]}; holding is {current.get(key, 0)}"
            )
    return _sequence(orders)

