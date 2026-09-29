from __future__ import annotations

import logging

import httpx

from app.domain import ExecutionResult

logger = logging.getLogger("kalpi.notifications")


class NotificationService:
    def __init__(self, webhook_url: str | None, timeout: float = 5.0) -> None:
        self.webhook_url = webhook_url
        self.timeout = timeout

    async def send(self, result: ExecutionResult) -> None:
        payload = {
            "event": "portfolio.execution.completed",
            "execution": result.model_dump(mode="json"),
            "summary": {
                "total": len(result.orders),
                "successful": result.successful_orders,
                "failed": len(result.orders) - result.successful_orders,
            },
        }
        if not self.webhook_url:
            logger.info("execution_notification=%s", payload)
            return
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(self.webhook_url, json=payload)
                response.raise_for_status()
        except (httpx.HTTPError, ValueError) as exc:
            # Trade outcome is authoritative; notification failure is reported but does not
            # rewrite a successful execution as failed.
            logger.error("Webhook delivery failed for %s: %s", result.execution_id, exc)

