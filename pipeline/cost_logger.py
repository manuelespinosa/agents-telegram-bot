"""Insert LLM usage rows into cost_logs (BudgetGate ledger schema)."""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cost_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            cost REAL NOT NULL,
            model TEXT,
            tokens_in INTEGER,
            tokens_out INTEGER
        )
        """
    )
    conn.commit()


def log_usage(
    cost_db_path: str,
    *,
    model: str,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost: float = 0.0,
    timestamp: str | None = None,
) -> None:
    """Append one cost_logs row. Unknown cost → 0.0 with tokens still recorded."""
    path = Path(cost_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = timestamp or _utc_iso()
    try:
        with sqlite3.connect(str(path)) as conn:
            _ensure_schema(conn)
            conn.execute(
                """
                INSERT INTO cost_logs (timestamp, cost, model, tokens_in, tokens_out)
                VALUES (?, ?, ?, ?, ?)
                """,
                (ts, float(cost), model, int(tokens_in), int(tokens_out)),
            )
            conn.commit()
    except Exception:
        logger.exception("cost_logger.log_usage failed for model=%s", model)
