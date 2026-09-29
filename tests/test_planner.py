import pytest

from app.domain import ExecutionRequest, Holding, Side
from app.planner import PlanningError, plan_orders


def test_target_portfolio_calculates_delta_and_sells_first():
    request = ExecutionRequest.model_validate(
        {
            "broker": "mock",
            "mode": "FIRST_TIME",
            "target_portfolio": [
                {"symbol": "INFY", "quantity": 8},
                {"symbol": "HDFCBANK", "quantity": 2},
            ],
        }
    )
    holdings = [
        Holding(symbol="INFY", quantity=10),
        Holding(symbol="TCS", quantity=3),
    ]

    orders = plan_orders(request, holdings)

    assert [(o.symbol, o.side, o.quantity) for o in orders] == [
        ("INFY", Side.SELL, 2),
        ("TCS", Side.SELL, 3),
        ("HDFCBANK", Side.BUY, 2),
    ]
    assert [order.sequence for order in orders] == [1, 2, 3]


def test_equal_target_produces_no_orders():
    request = ExecutionRequest.model_validate(
        {
            "broker": "mock",
            "mode": "FIRST_TIME",
            "target_portfolio": [{"symbol": "INFY", "quantity": 10}],
        }
    )
    assert plan_orders(request, [Holding(symbol="INFY", quantity=10)]) == []


def test_rebalance_uses_explicit_quantities_without_delta_calculation():
    request = ExecutionRequest.model_validate(
        {
            "broker": "mock",
            "mode": "REBALANCE",
            "instructions": [
                {"action": "ADJUST_BUY", "symbol": "INFY", "quantity": 3},
                {"action": "ADJUST_SELL", "symbol": "TCS", "quantity": 2},
            ],
        }
    )

    orders = plan_orders(request, [Holding(symbol="TCS", quantity=10)])

    assert [(o.symbol, o.side, o.quantity) for o in orders] == [
        ("TCS", Side.SELL, 2),
        ("INFY", Side.BUY, 3),
    ]


def test_rebalance_rejects_oversell():
    request = ExecutionRequest.model_validate(
        {
            "broker": "mock",
            "mode": "REBALANCE",
            "instructions": [{"action": "SELL", "symbol": "TCS", "quantity": 11}],
        }
    )

    with pytest.raises(PlanningError, match="Cannot sell 11"):
        plan_orders(request, [Holding(symbol="TCS", quantity=10)])


def test_duplicate_target_is_rejected():
    request = ExecutionRequest.model_validate(
        {
            "broker": "mock",
            "mode": "FIRST_TIME",
            "target_portfolio": [
                {"symbol": "INFY", "quantity": 1},
                {"symbol": "infy", "quantity": 2},
            ],
        }
    )
    with pytest.raises(PlanningError, match="Duplicate target"):
        plan_orders(request, [])

