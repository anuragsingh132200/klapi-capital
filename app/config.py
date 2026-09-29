from __future__ import annotations

import os
from dataclasses import dataclass


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_env: str = "development"
    database_path: str = "./kalpi.db"
    encryption_secret: str = "development-only-change-me"
    enable_live_trading: bool = False
    webhook_url: str | None = None
    broker_timeout_seconds: float = 10.0
    max_order_attempts: int = 3
    order_poll_interval_seconds: float = 1.0
    order_poll_timeout_seconds: float = 20.0

    @classmethod
    def from_env(cls, *, database_path: str | None = None) -> "Settings":
        return cls(
            app_env=os.getenv("APP_ENV", "development"),
            database_path=database_path or os.getenv("DATABASE_PATH", "./kalpi.db"),
            encryption_secret=os.getenv(
                "ENCRYPTION_SECRET", "development-only-change-me"
            ),
            enable_live_trading=_as_bool(os.getenv("ENABLE_LIVE_TRADING", "false")),
            webhook_url=os.getenv("WEBHOOK_URL") or None,
            broker_timeout_seconds=float(os.getenv("BROKER_TIMEOUT_SECONDS", "10")),
            max_order_attempts=int(os.getenv("MAX_ORDER_ATTEMPTS", "3")),
            order_poll_interval_seconds=float(
                os.getenv("ORDER_POLL_INTERVAL_SECONDS", "1")
            ),
            order_poll_timeout_seconds=float(os.getenv("ORDER_POLL_TIMEOUT_SECONDS", "20")),
        )
