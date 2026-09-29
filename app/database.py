from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from app.domain import ExecutionResult


SCHEMA = """
CREATE TABLE IF NOT EXISTS broker_connections (
    broker TEXT PRIMARY KEY,
    encrypted_credentials TEXT NOT NULL,
    connected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS executions (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    broker TEXT NOT NULL,
    mode TEXT NOT NULL,
    request_json TEXT NOT NULL,
    result_json TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_executions_created_at
ON executions(created_at DESC);

CREATE TABLE IF NOT EXISTS broker_auth_states (
    state TEXT PRIMARY KEY,
    broker TEXT NOT NULL,
    encrypted_config TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT
);
"""


class Database:
    """Small persistent store for a single-instance assignment deployment.

    The repository interface deliberately keeps SQLite isolated. It can be replaced by
    PostgreSQL without touching API, planner, broker, or execution code.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None

    def initialize(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.executescript(SCHEMA)
            self._connection.commit()

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("Database has not been initialized")
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def save_broker_credentials(self, broker: str, encrypted_credentials: str) -> None:
        with self._lock:
            self.connection.execute(
                """
                INSERT INTO broker_connections(broker, encrypted_credentials)
                VALUES (?, ?)
                ON CONFLICT(broker) DO UPDATE SET
                    encrypted_credentials = excluded.encrypted_credentials,
                    connected_at = CURRENT_TIMESTAMP
                """,
                (broker, encrypted_credentials),
            )
            self.connection.commit()

    def get_broker_credentials(self, broker: str) -> str | None:
        with self._lock:
            row = self.connection.execute(
                "SELECT encrypted_credentials FROM broker_connections WHERE broker = ?",
                (broker,),
            ).fetchone()
        return str(row["encrypted_credentials"]) if row else None

    def delete_broker_connection(self, broker: str) -> bool:
        with self._lock:
            cursor = self.connection.execute(
                "DELETE FROM broker_connections WHERE broker = ?", (broker,)
            )
            self.connection.commit()
        return cursor.rowcount > 0

    def save_auth_state(
        self, *, state: str, broker: str, encrypted_config: str, expires_at: str
    ) -> None:
        with self._lock:
            self.connection.execute(
                """
                INSERT INTO broker_auth_states(state, broker, encrypted_config, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (state, broker, encrypted_config, expires_at),
            )
            self.connection.commit()

    def consume_auth_state(self, state: str, broker: str) -> tuple[str, str] | None:
        """Atomically consume an OAuth state to prevent callback replay."""
        with self._lock:
            row = self.connection.execute(
                """
                SELECT encrypted_config, expires_at FROM broker_auth_states
                WHERE state = ? AND broker = ? AND consumed_at IS NULL
                """,
                (state, broker),
            ).fetchone()
            if not row:
                return None
            # Delete the row so application secrets are not retained after the one-time
            # callback and the same state can never be replayed.
            self.connection.execute("DELETE FROM broker_auth_states WHERE state = ?", (state,))
            self.connection.commit()
        return str(row["encrypted_config"]), str(row["expires_at"])

    def create_execution(
        self,
        *,
        execution_id: str,
        idempotency_key: str,
        broker: str,
        mode: str,
        request: dict[str, Any],
    ) -> bool:
        try:
            with self._lock:
                self.connection.execute(
                    """
                    INSERT INTO executions(
                        id, idempotency_key, broker, mode, request_json, status
                    ) VALUES (?, ?, ?, ?, ?, 'PENDING')
                    """,
                    (
                        execution_id,
                        idempotency_key,
                        broker,
                        mode,
                        json.dumps(request, default=str),
                    ),
                )
                self.connection.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def save_execution_result(self, result: ExecutionResult) -> None:
        with self._lock:
            self.connection.execute(
                """
                UPDATE executions
                SET result_json = ?, status = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    result.model_dump_json(),
                    result.status.value,
                    result.completed_at.isoformat() if result.completed_at else None,
                    result.execution_id,
                ),
            )
            self.connection.commit()

    def delete_pending_execution(self, execution_id: str) -> None:
        """Release an idempotency reservation when no broker order was attempted."""
        with self._lock:
            self.connection.execute(
                "DELETE FROM executions WHERE id = ? AND status = 'PENDING'",
                (execution_id,),
            )
            self.connection.commit()

    def get_by_idempotency_key(self, key: str) -> ExecutionResult | None:
        with self._lock:
            row = self.connection.execute(
                "SELECT result_json FROM executions WHERE idempotency_key = ?", (key,)
            ).fetchone()
        if not row or not row["result_json"]:
            return None
        return ExecutionResult.model_validate_json(row["result_json"])

    def get_execution(self, execution_id: str) -> ExecutionResult | None:
        with self._lock:
            row = self.connection.execute(
                "SELECT result_json FROM executions WHERE id = ?", (execution_id,)
            ).fetchone()
        if not row or not row["result_json"]:
            return None
        return ExecutionResult.model_validate_json(row["result_json"])

    def list_executions(self, limit: int = 20) -> list[ExecutionResult]:
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT result_json FROM executions
                WHERE result_json IS NOT NULL
                ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [ExecutionResult.model_validate_json(row["result_json"]) for row in rows]
