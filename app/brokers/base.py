from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.domain import BrokerOrderResult, Holding, PlannedOrder


class BrokerError(RuntimeError):
    pass


class BrokerAuthenticationError(BrokerError):
    pass


class BrokerRateLimitError(BrokerError):
    pass


class BrokerTransientError(BrokerError):
    pass


class BrokerUncertainOrderError(BrokerError):
    """The request outcome is unknown and must not be blindly retried."""


class BrokerAdapter(ABC):
    def __init__(self, credentials: dict[str, Any], timeout: float = 10.0) -> None:
        self.credentials = credentials
        self.timeout = timeout

    @abstractmethod
    async def validate_credentials(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_holdings(self) -> list[Holding]:
        raise NotImplementedError

    @abstractmethod
    async def place_order(self, order: PlannedOrder) -> BrokerOrderResult:
        raise NotImplementedError

    @abstractmethod
    async def get_order_status(self, order_id: str) -> BrokerOrderResult:
        raise NotImplementedError

    async def close(self) -> None:
        return None

