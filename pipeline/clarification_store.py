"""SQLite multi-turn clarification state with short TTL (D-04)."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import settings


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        dt = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class ClarificationState:
    chat_id: int
    user_id: int
    original_text: str
    question: str
    partial_decision_json: str | None
    created_at: str
    expires_at: str


class ClarificationStore:
    """One pending Q&A per chat_id; expired rows treated as absent."""

    def __init__(
        self,
        db_path: str,
        ttl_sec: int | None = None,
        now_fn=None,
    ):
        self.db_path = db_path
        self.ttl_sec = int(
            ttl_sec if ttl_sec is not None else settings.clarification_ttl_sec
        )
        self.now_fn = now_fn or _utc_now
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS clarification_state (
                    chat_id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    original_text TEXT NOT NULL,
                    question TEXT NOT NULL,
                    partial_decision_json TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def set(
        self,
        chat_id: int,
        user_id: int,
        original_text: str,
        question: str,
        partial_decision_json: str | None = None,
    ) -> ClarificationState:
        now = self.now_fn()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        expires = now + timedelta(seconds=self.ttl_sec)
        created_at = _iso(now)
        expires_at = _iso(expires)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO clarification_state (
                    chat_id, user_id, original_text, question,
                    partial_decision_json, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    user_id=excluded.user_id,
                    original_text=excluded.original_text,
                    question=excluded.question,
                    partial_decision_json=excluded.partial_decision_json,
                    created_at=excluded.created_at,
                    expires_at=excluded.expires_at
                """,
                (
                    int(chat_id),
                    int(user_id),
                    original_text,
                    question,
                    partial_decision_json,
                    created_at,
                    expires_at,
                ),
            )
            conn.commit()
        return ClarificationState(
            chat_id=int(chat_id),
            user_id=int(user_id),
            original_text=original_text,
            question=question,
            partial_decision_json=partial_decision_json,
            created_at=created_at,
            expires_at=expires_at,
        )

    def get(self, chat_id: int) -> ClarificationState | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM clarification_state WHERE chat_id = ?",
                (int(chat_id),),
            ).fetchone()
        if row is None:
            return None
        now = self.now_fn()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        expires = _parse_iso(row["expires_at"])
        if now >= expires:
            self.cancel(int(chat_id))
            return None
        return ClarificationState(
            chat_id=int(row["chat_id"]),
            user_id=int(row["user_id"]),
            original_text=row["original_text"],
            question=row["question"],
            partial_decision_json=row["partial_decision_json"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )

    def cancel(self, chat_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM clarification_state WHERE chat_id = ?",
                (int(chat_id),),
            )
            conn.commit()
