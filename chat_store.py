"""Persist known Telegram chat recipients (CostTracker-style SQLite)."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PATH = "/app/data/known_chats.sqlite"


class ChatStore:
    """Append+persist store for authorized chat ids used by daily reports."""

    def __init__(self, path: str = DEFAULT_PATH):
        self.path = path
        parent = Path(path).parent
        parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS known_chats (
                    chat_id INTEGER PRIMARY KEY,
                    first_seen TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            conn.commit()

    def add_chat(self, chat_id: int) -> None:
        """Record a chat id (idempotent)."""
        if chat_id is None:
            return
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO known_chats (chat_id) VALUES (?)",
                (int(chat_id),),
            )
            conn.commit()
        logger.debug("ChatStore: recorded chat_id=%s", chat_id)

    def list_chats(self) -> list[int]:
        """Return all known chat ids."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT chat_id FROM known_chats ORDER BY chat_id"
            ).fetchall()
        return [int(r["chat_id"]) for r in rows]
