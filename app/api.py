from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request, status

from app.brokers.base import BrokerAuthenticationError, BrokerError, BrokerRateLimitError
from app.brokers.factory import BrokerNotConnectedError
from app.domain import (
    BrokerAuthStartRequest,
    BrokerAuthStartResponse,
    BrokerConnectionRequest,
    BrokerConnectionResponse,
    BrokerName,
    ExecutionRequest,
    ExecutionResult,
    GrowwTokenRequest,
)
from app.engine import DuplicateExecutionInProgress
from app.planner import PlanningError

router = APIRouter(prefix="/api/v1")


@router.post(
    "/brokers/{broker}/auth/start",
    response_model=BrokerAuthStartResponse,
    tags=["broker authentication"],
)
async def start_broker_auth(
    broker: BrokerName, payload: BrokerAuthStartRequest, request: Request
) -> BrokerAuthStartResponse:
    try:
        return request.app.state.broker_auth.start(broker, payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get(
    "/brokers/{broker}/auth/callback",
    response_model=BrokerConnectionResponse,
    tags=["broker authentication"],
)
async def broker_auth_callback(
    broker: BrokerName,
    request: Request,
    state: str,
    code: str | None = None,
    request_token: str | None = None,
    auth_token: str | None = None,
    feed_token: str | None = None,
) -> BrokerConnectionResponse:
    try:
        credentials = await request.app.state.broker_auth.exchange_callback(
            broker,
            state=state,
            code=code,
            request_token=request_token,
            auth_token=auth_token,
            feed_token=feed_token,
        )
        await request.app.state.broker_factory.connect(broker, credentials, validate=True)
    except BrokerAuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except BrokerRateLimitError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    except BrokerError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return BrokerConnectionResponse(
        broker=broker, connected=True, message="Broker authentication completed"
    )


@router.post(
    "/brokers/groww/auth/token",
    response_model=BrokerConnectionResponse,
    tags=["broker authentication"],
)
async def generate_groww_token(
    payload: GrowwTokenRequest, request: Request
) -> BrokerConnectionResponse:
    try:
        credentials = await request.app.state.broker_auth.generate_groww_token(payload)
        await request.app.state.broker_factory.connect(
            BrokerName.GROWW, credentials, validate=True
        )
    except BrokerAuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except BrokerRateLimitError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    except BrokerError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return BrokerConnectionResponse(
        broker=BrokerName.GROWW,
        connected=True,
        message="Groww authentication completed",
    )


@router.get("/brokers", tags=["brokers"])
async def list_brokers(request: Request) -> dict:
    factory = request.app.state.broker_factory
    return {
        "live_trading_enabled": request.app.state.settings.enable_live_trading,
        "brokers": [
            {
                "name": broker.value,
                "connected": factory.is_connected(broker),
                "mode": "simulated" if broker == BrokerName.MOCK else "live",
            }
            for broker in BrokerName
        ],
    }


@router.post(
    "/brokers/{broker}/connect",
    response_model=BrokerConnectionResponse,
    tags=["brokers"],
)
async def connect_broker(
    broker: BrokerName, payload: BrokerConnectionRequest, request: Request
) -> BrokerConnectionResponse:
    try:
        await request.app.state.broker_factory.connect(
            broker, payload.credentials, validate=payload.validate_connection
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Missing or invalid credentials: {exc}",
        ) from exc
    except BrokerAuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except BrokerError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return BrokerConnectionResponse(
        broker=broker,
        connected=True,
        message=(
            "Mock broker connected"
            if broker == BrokerName.MOCK
            else "Broker session validated and credentials encrypted at rest"
        ),
    )


@router.get("/brokers/{broker}/status", tags=["brokers"])
async def broker_status(broker: BrokerName, request: Request) -> dict:
    return {
        "broker": broker.value,
        "connected": request.app.state.broker_factory.is_connected(broker),
    }


@router.delete("/brokers/{broker}/connection", tags=["brokers"])
async def disconnect_broker(broker: BrokerName, request: Request) -> dict:
    deleted = await request.app.state.broker_factory.disconnect(broker)
    return {"broker": broker.value, "connected": False, "deleted": deleted}


@router.post(
    "/executions",
    response_model=ExecutionResult,
    status_code=status.HTTP_201_CREATED,
    tags=["executions"],
)
async def create_execution(
    payload: ExecutionRequest,
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=100,
            description="Unique client key. Replays return the original result.",
        ),
    ],
) -> ExecutionResult:
    try:
        return await request.app.state.engine.execute(payload, idempotency_key)
    except BrokerNotConnectedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except DuplicateExecutionInProgress as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except PlanningError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except BrokerAuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except BrokerError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/executions/{execution_id}", response_model=ExecutionResult, tags=["executions"])
async def get_execution(execution_id: str, request: Request) -> ExecutionResult:
    result = request.app.state.database.get_execution(execution_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution not found")
    return result


@router.get("/executions", response_model=list[ExecutionResult], tags=["executions"])
async def list_executions(
    request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[ExecutionResult]:
    return request.app.state.database.list_executions(limit)
