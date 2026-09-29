from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BrokerName(str, Enum):
    MOCK = "mock"
    ZERODHA = "zerodha"
    FYERS = "fyers"
    ANGELONE = "angelone"
    GROWW = "groww"
    UPSTOX = "upstox"


class ExecutionMode(str, Enum):
    FIRST_TIME = "FIRST_TIME"
    REBALANCE = "REBALANCE"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class RebalanceAction(str, Enum):
    SELL = "SELL"
    BUY_NEW = "BUY_NEW"
    ADJUST_BUY = "ADJUST_BUY"
    ADJUST_SELL = "ADJUST_SELL"


class OrderStatus(str, Enum):
    PLANNED = "PLANNED"
    ACCEPTED = "ACCEPTED"
    COMPLETE = "COMPLETE"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class ExecutionStatus(str, Enum):
    PENDING = "PENDING"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    FAILED = "FAILED"
    NO_ACTION = "NO_ACTION"


class Holding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    quantity: int = Field(ge=0)
    exchange: str = "NSE"


class TargetPosition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=40)
    quantity: int = Field(ge=0)
    exchange: str = "NSE"


class RebalanceInstruction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: RebalanceAction
    symbol: str = Field(min_length=1, max_length=40)
    quantity: int = Field(gt=0)
    exchange: str = "NSE"


class ExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    broker: BrokerName
    mode: ExecutionMode
    target_portfolio: list[TargetPosition] | None = None
    instructions: list[RebalanceInstruction] | None = None

    @model_validator(mode="after")
    def validate_mode_payload(self) -> "ExecutionRequest":
        if self.mode == ExecutionMode.FIRST_TIME:
            if not self.target_portfolio:
                raise ValueError("target_portfolio is required for FIRST_TIME mode")
            if self.instructions:
                raise ValueError("instructions are not allowed for FIRST_TIME mode")
        else:
            if not self.instructions:
                raise ValueError("instructions are required for REBALANCE mode")
            if self.target_portfolio:
                raise ValueError("target_portfolio is not allowed for REBALANCE mode")
        return self


class PlannedOrder(BaseModel):
    symbol: str
    exchange: str
    side: Side
    quantity: int = Field(gt=0)
    sequence: int = 0
    client_order_id: str | None = None


class BrokerOrderResult(BaseModel):
    broker_order_id: str | None = None
    status: OrderStatus
    message: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class OrderResult(BaseModel):
    symbol: str
    exchange: str
    side: Side
    quantity: int
    status: OrderStatus
    broker_order_id: str | None = None
    message: str | None = None
    attempts: int = 1


class ExecutionResult(BaseModel):
    execution_id: str
    broker: BrokerName
    mode: ExecutionMode
    status: ExecutionStatus
    orders: list[OrderResult]
    created_at: datetime
    completed_at: datetime | None = None

    @property
    def successful_orders(self) -> int:
        return sum(o.status == OrderStatus.COMPLETE for o in self.orders)


class BrokerConnectionRequest(BaseModel):
    credentials: dict[str, Any] = Field(default_factory=dict)
    validate_connection: bool = True


class BrokerConnectionResponse(BaseModel):
    broker: BrokerName
    connected: bool
    message: str


class BrokerAuthStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str = Field(min_length=1)
    api_secret: str | None = None
    redirect_uri: str = Field(min_length=8)


class BrokerAuthStartResponse(BaseModel):
    broker: BrokerName
    authorization_url: str
    state: str
    expires_at: datetime


class GrowwTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str = Field(min_length=1)
    api_secret: str | None = None
    totp: str | None = None

    @model_validator(mode="after")
    def validate_auth_method(self) -> "GrowwTokenRequest":
        if bool(self.api_secret) == bool(self.totp):
            raise ValueError("Provide exactly one of api_secret or totp")
        return self
