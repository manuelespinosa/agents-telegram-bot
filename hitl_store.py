"""SQLite HITL request queue + audit log (HITL-02 foundation). Pure Python."""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_PATH = "/app/data/hitl.sqlite"

VALID_STATUSES = frozenset(
    {"pending", "approved", "rejected", "expired", "executed", "failed"}
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class HITLRequest:
    request_id: str
    action_id: str
    params_json: str
    risk_tier: str
    requires_deepseek: bool
    reason: str
    payload_canonical: str
    payload_hmac: str
    status: str
    created_at: str
    expires_at: str
    decided_at: str | None = None
    decided_by: str | None = None
    executed_at: str | None = None
    execution_result: str | None = None
    chat_id: int | None = None
    message_id: int | None = None

    def params(self) -> dict[str, Any]:
        return json.loads(self.params_json)


class HITLStore:
    """Persisted HITL queue with atomic single-use status transitions."""

    def __init__(self, path: str = DEFAULT_PATH):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS hitl_requests (
                    request_id TEXT PRIMARY KEY,
                    action_id TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    risk_tier TEXT NOT NULL,
                    requires_deepseek INTEGER NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL,
                    payload_canonical TEXT NOT NULL,
                    payload_hmac TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    decided_at TEXT,
                    decided_by TEXT,
                    executed_at TEXT,
                    execution_result TEXT,
                    chat_id INTEGER,
                    message_id INTEGER
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    event TEXT NOT NULL,
                    detail_json TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def create_request(
        self,
        *,
        request_id: str,
        action_id: str,
        params: dict[str, Any],
        risk_tier: str,
        requires_deepseek: bool,
        reason: str,
        payload_canonical: str,
        payload_hmac: str,
        expires_at: str,
        chat_id: int | None = None,
        message_id: int | None = None,
    ) -> HITLRequest:
        created_at = _utc_now_iso()
        params_json = json.dumps(params, sort_keys=True, separators=(",", ":"))
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO hitl_requests (
                    request_id, action_id, params_json, risk_tier,
                    requires_deepseek, reason, payload_canonical, payload_hmac,
                    status, created_at, expires_at, chat_id, message_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                """,
                (
                    request_id,
                    action_id,
                    params_json,
                    risk_tier,
                    1 if requires_deepseek else 0,
                    reason,
                    payload_canonical,
                    payload_hmac,
                    created_at,
                    expires_at,
                    chat_id,
                    message_id,
                ),
            )
            conn.commit()
        self.audit(
            "hitl_created",
            {
                "request_id": request_id,
                "action_id": action_id,
                "risk_tier": risk_tier,
            },
        )
        loaded = self.get(request_id)
        assert loaded is not None
        return loaded

    def get(self, request_id: str) -> HITLRequest | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM hitl_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_request(row)

    @staticmethod
    def _row_to_request(row: sqlite3.Row) -> HITLRequest:
        return HITLRequest(
            request_id=row["request_id"],
            action_id=row["action_id"],
            params_json=row["params_json"],
            risk_tier=row["risk_tier"],
            requires_deepseek=bool(row["requires_deepseek"]),
            reason=row["reason"],
            payload_canonical=row["payload_canonical"],
            payload_hmac=row["payload_hmac"],
            status=row["status"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            decided_at=row["decided_at"],
            decided_by=row["decided_by"],
            executed_at=row["executed_at"],
            execution_result=row["execution_result"],
            chat_id=row["chat_id"],
            message_id=row["message_id"],
        )

    def expire_if_pending(self, request_id: str) -> bool:
        """Transition pending → expired only. Never auto-executes (D-07)."""
        now = _utc_now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE hitl_requests
                SET status = 'expired', decided_at = ?
                WHERE request_id = ? AND status = 'pending'
                """,
                (now, request_id),
            )
            conn.commit()
            changed = cur.rowcount > 0
        if changed:
            self.audit("expire", {"request_id": request_id})
        return changed

    def mark_approved(self, request_id: str, decided_by: str) -> bool:
        now = _utc_now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE hitl_requests
                SET status = 'approved', decided_at = ?, decided_by = ?
                WHERE request_id = ? AND status = 'pending'
                """,
                (now, str(decided_by), request_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def mark_rejected(self, request_id: str, decided_by: str) -> bool:
        now = _utc_now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE hitl_requests
                SET status = 'rejected', decided_at = ?, decided_by = ?
                WHERE request_id = ? AND status = 'pending'
                """,
                (now, str(decided_by), request_id),
            )
            conn.commit()
            changed = cur.rowcount > 0
        if changed:
            self.audit("reject", {"request_id": request_id, "decided_by": decided_by})
        return changed

    def mark_executed(self, request_id: str, result: str) -> bool:
        now = _utc_now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE hitl_requests
                SET status = 'executed', executed_at = ?, execution_result = ?
                WHERE request_id = ? AND status = 'approved'
                """,
                (now, result, request_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def mark_failed(self, request_id: str, result: str) -> bool:
        now = _utc_now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE hitl_requests
                SET status = 'failed', executed_at = ?, execution_result = ?
                WHERE request_id = ? AND status IN ('approved', 'pending')
                """,
                (now, result, request_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def bind_telegram_message(
        self, request_id: str, chat_id: int, message_id: int
    ) -> bool:
        """Attach Telegram chat/message ids for expire/edit callbacks."""
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE hitl_requests
                SET chat_id = ?, message_id = ?
                WHERE request_id = ?
                """,
                (int(chat_id), int(message_id), request_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def audit(self, event: str, detail: dict[str, Any]) -> None:
        """Append an audit_log row. Never logs secrets."""
        ts = _utc_now_iso()
        detail_json = json.dumps(detail, sort_keys=True, separators=(",", ":"))
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO audit_log (ts, event, detail_json) VALUES (?, ?, ?)",
                (ts, event, detail_json),
            )
            conn.commit()
        logger.debug("HITL audit event=%s", event)

    def list_audit_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, ts, event, detail_json FROM audit_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "ts": r["ts"],
                "event": r["event"],
                "detail": json.loads(r["detail_json"]),
            }
            for r in rows
        ]
