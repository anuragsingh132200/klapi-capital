from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import router
from app.brokers.factory import BrokerFactory
from app.config import Settings
from app.database import Database
from app.engine import ExecutionEngine
from app.notifications import NotificationService
from app.security import CredentialVault

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def create_app(*, database_path: str | None = None) -> FastAPI:
    settings = Settings.from_env(database_path=database_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = Database(settings.database_path)
        database.initialize()
        vault = CredentialVault(settings.encryption_secret)
        broker_factory = BrokerFactory(database, vault, settings)
        notifier = NotificationService(settings.webhook_url)
        app.state.settings = settings
        app.state.database = database
        app.state.broker_factory = broker_factory
        app.state.engine = ExecutionEngine(
            database,
            broker_factory,
            notifier,
            max_attempts=settings.max_order_attempts,
        )
        yield
        await broker_factory.close()
        database.close()

    app = FastAPI(
        title="Kalpi Portfolio Execution Engine",
        version="1.0.0",
        description=(
            "Broker-neutral target portfolio and explicit rebalance execution. "
            "Live orders require an explicit server-side safety switch."
        ),
        lifespan=lifespan,
    )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logging.getLogger("kalpi.api").exception(
            "Unhandled error for %s %s", request.method, request.url.path
        )
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    @app.get("/health/live", tags=["health"])
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    async def readiness(request: Request) -> dict[str, str]:
        request.app.state.database.connection.execute("SELECT 1").fetchone()
        return {"status": "ready"}

    app.include_router(router)
    static_dir = Path(__file__).parent / "static"
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
    return app


app = create_app()

