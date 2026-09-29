from __future__ import annotations

from app.brokers.base import BrokerAdapter
from app.brokers.live import (
    AngelOneAdapter,
    FyersAdapter,
    GrowwAdapter,
    UpstoxAdapter,
    ZerodhaAdapter,
)
from app.brokers.mock import MockBrokerAdapter
from app.config import Settings
from app.database import Database
from app.domain import BrokerName
from app.security import CredentialVault


ADAPTERS: dict[BrokerName, type[BrokerAdapter]] = {
    BrokerName.MOCK: MockBrokerAdapter,
    BrokerName.ZERODHA: ZerodhaAdapter,
    BrokerName.FYERS: FyersAdapter,
    BrokerName.ANGELONE: AngelOneAdapter,
    BrokerName.GROWW: GrowwAdapter,
    BrokerName.UPSTOX: UpstoxAdapter,
}


class BrokerNotConnectedError(RuntimeError):
    pass


class BrokerFactory:
    def __init__(self, database: Database, vault: CredentialVault, settings: Settings) -> None:
        self.database = database
        self.vault = vault
        self.settings = settings
        self._instances: dict[BrokerName, BrokerAdapter] = {}

    def _build(self, broker: BrokerName, credentials: dict) -> BrokerAdapter:
        runtime_credentials = dict(credentials)
        runtime_credentials["_live_trading_enabled"] = self.settings.enable_live_trading
        return ADAPTERS[broker](runtime_credentials, self.settings.broker_timeout_seconds)

    async def connect(
        self, broker: BrokerName, credentials: dict, *, validate: bool = True
    ) -> BrokerAdapter:
        adapter = self._build(broker, credentials)
        if validate:
            await adapter.validate_credentials()
        previous = self._instances.pop(broker, None)
        if previous:
            await previous.close()
        self.database.save_broker_credentials(broker.value, self.vault.encrypt(credentials))
        self._instances[broker] = adapter
        return adapter

    def is_connected(self, broker: BrokerName) -> bool:
        return (
            broker in self._instances
            or self.database.get_broker_credentials(broker.value) is not None
        )

    def get(self, broker: BrokerName) -> BrokerAdapter:
        if broker in self._instances:
            return self._instances[broker]
        encrypted = self.database.get_broker_credentials(broker.value)
        if encrypted is None:
            raise BrokerNotConnectedError(f"Broker '{broker.value}' is not connected")
        adapter = self._build(broker, self.vault.decrypt(encrypted))
        self._instances[broker] = adapter
        return adapter

    async def disconnect(self, broker: BrokerName) -> bool:
        adapter = self._instances.pop(broker, None)
        if adapter:
            await adapter.close()
        return self.database.delete_broker_connection(broker.value)

    async def close(self) -> None:
        for adapter in self._instances.values():
            await adapter.close()
        self._instances.clear()

