from contextlib import asynccontextmanager

import httpx
import pytest

from app.main import create_app


@asynccontextmanager
async def app_client(app):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client


async def connect_mock(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/brokers/mock/connect",
        json={
            "credentials": {
                "holdings": [{"symbol": "TCS", "exchange": "NSE", "quantity": 8}]
            },
            "validate_connection": True,
        },
    )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_health_and_first_time_execution(tmp_path):
    app = create_app(database_path=str(tmp_path / "test.db"))
    async with app_client(app) as client:
        assert (await client.get("/health/ready")).json() == {"status": "ready"}
        await connect_mock(client)
        response = await client.post(
            "/api/v1/executions",
            headers={"Idempotency-Key": "first-time-001"},
            json={
                "broker": "mock",
                "mode": "FIRST_TIME",
                "target_portfolio": [
                    {"symbol": "TCS", "quantity": 5},
                    {"symbol": "INFY", "quantity": 4},
                ],
            },
        )
        assert response.status_code == 201, response.text
        data = response.json()
        assert data["status"] == "COMPLETED"
        assert [(order["side"], order["symbol"]) for order in data["orders"]] == [
            ("SELL", "TCS"),
            ("BUY", "INFY"),
        ]


@pytest.mark.asyncio
async def test_idempotency_returns_same_execution(tmp_path):
    app = create_app(database_path=str(tmp_path / "test.db"))
    async with app_client(app) as client:
        await connect_mock(client)
        payload = {
            "broker": "mock",
            "mode": "FIRST_TIME",
            "target_portfolio": [{"symbol": "INFY", "quantity": 2}],
        }
        headers = {"Idempotency-Key": "same-request-123"}
        first = await client.post("/api/v1/executions", headers=headers, json=payload)
        second = await client.post("/api/v1/executions", headers=headers, json=payload)
        assert first.status_code == second.status_code == 201
        assert first.json()["execution_id"] == second.json()["execution_id"]
        assert first.json()["orders"] == second.json()["orders"]


@pytest.mark.asyncio
async def test_partial_failure_and_rate_limit_retry(tmp_path):
    app = create_app(database_path=str(tmp_path / "test.db"))
    async with app_client(app) as client:
        await connect_mock(client)
        response = await client.post(
            "/api/v1/executions",
            headers={"Idempotency-Key": "partial-failure-1"},
            json={
                "broker": "mock",
                "mode": "REBALANCE",
                "instructions": [
                    {"action": "BUY_NEW", "symbol": "RATEBANK", "quantity": 1},
                    {"action": "BUY_NEW", "symbol": "FAIL-DEMO", "quantity": 1},
                ],
            },
        )
        assert response.status_code == 201, response.text
        data = response.json()
        assert data["status"] == "PARTIALLY_COMPLETED"
        by_symbol = {item["symbol"]: item for item in data["orders"]}
        assert by_symbol["RATEBANK"]["status"] == "COMPLETE"
        assert by_symbol["RATEBANK"]["attempts"] == 2
        assert by_symbol["FAIL-DEMO"]["status"] == "REJECTED"


@pytest.mark.asyncio
async def test_execution_requires_connection_and_idempotency_key(tmp_path):
    app = create_app(database_path=str(tmp_path / "test.db"))
    payload = {
        "broker": "mock",
        "mode": "FIRST_TIME",
        "target_portfolio": [{"symbol": "INFY", "quantity": 1}],
    }
    async with app_client(app) as client:
        assert (await client.post("/api/v1/executions", json=payload)).status_code == 422
        response = await client.post(
            "/api/v1/executions",
            headers={"Idempotency-Key": "not-connected-1"},
            json=payload,
        )
        assert response.status_code == 409


@pytest.mark.asyncio
async def test_planning_failure_releases_idempotency_key(tmp_path):
    app = create_app(database_path=str(tmp_path / "test.db"))
    headers = {"Idempotency-Key": "correctable-request-1"}
    async with app_client(app) as client:
        await connect_mock(client)
        invalid = await client.post(
            "/api/v1/executions",
            headers=headers,
            json={
                "broker": "mock",
                "mode": "REBALANCE",
                "instructions": [
                    {"action": "SELL", "symbol": "TCS", "quantity": 99}
                ],
            },
        )
        assert invalid.status_code == 422

        corrected = await client.post(
            "/api/v1/executions",
            headers=headers,
            json={
                "broker": "mock",
                "mode": "REBALANCE",
                "instructions": [
                    {"action": "SELL", "symbol": "TCS", "quantity": 2}
                ],
            },
        )
        assert corrected.status_code == 201
        assert corrected.json()["status"] == "COMPLETED"
