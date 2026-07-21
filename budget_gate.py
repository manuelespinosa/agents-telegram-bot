"""Rolling 24h budget kill-switch (HITL-05). Pure Python — mutations only."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MAX_USD = 0.50
SOFT_WARN_RATIO = 0.80


class BudgetGate:
    """Gate mutations when rolling 24h cost >= max or explicit pause flag set."""

    def __init__(
        self,
        cost_db_path: str,
        state_db_path: str | None = None,
        max_usd: float = DEFAULT_MAX_USD,
    ):
        self.cost_db_path = cost_db_path
        self.state_db_path = state_db_path or cost_db_path
        self.max_usd = float(max_usd)
        Path(self.cost_db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.state_db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_cost_db()
        self._init_state_db()

    def _connect_cost(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.cost_db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _connect_state(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.state_db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_cost_db(self) -> None:
        """Ensure Phase-1-compatible cost_logs table exists."""
        with self._connect_cost() as conn:
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

    def _init_state_db(self) -> None:
        with self._connect_state() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS budget_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    paused INTEGER NOT NULL DEFAULT 0,
                    paused_at TEXT,
                    pause_reason TEXT
                )
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO budget_state (id, paused) VALUES (1, 0)"
            )
            conn.commit()

    def rolling_cost_24h(self) -> float:
        with self._connect_cost() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(cost), 0) AS total FROM cost_logs
                WHERE timestamp >= datetime('now', '-24 hours')
                """
            ).fetchone()
        return float(row["total"] if row else 0.0)

    def is_paused(self) -> bool:
        with self._connect_state() as conn:
            row = conn.execute(
                "SELECT paused FROM budget_state WHERE id = 1"
            ).fetchone()
        if row is None:
            return False
        return bool(row["paused"])

    def _set_paused(self, paused: bool, reason: str | None = None) -> None:
        with self._connect_state() as conn:
            if paused:
                conn.execute(
                    """
                    UPDATE budget_state
                    SET paused = 1,
                        paused_at = datetime('now'),
                        pause_reason = ?
                    WHERE id = 1
                    """,
                    (reason or "budget_exceeded",),
                )
            else:
                conn.execute(
                    """
                    UPDATE budget_state
                    SET paused = 0, paused_at = NULL, pause_reason = NULL
                    WHERE id = 1
                    """
                )
            conn.commit()

    def mutations_allowed(self) -> bool:
        """False if paused OR rolling cost >= max. Reads are not gated here."""
        if self.is_paused():
            return False
        return self.rolling_cost_24h() < self.max_usd

    def check_and_trip(self) -> str | None:
        """If over cap and not yet paused, set pause and return alert text."""
        cost = self.rolling_cost_24h()
        if cost >= self.max_usd and not self.is_paused():
            self._set_paused(True, reason="budget_exceeded")
            return (
                f"🛑 Budget kill-switch\n"
                f"Coste 24h: ${cost:.4f} / ${self.max_usd:.2f}\n"
                f"Mutaciones pausadas. Monitor read-only sigue activo.\n"
                f"Reanudar: /resume-budget"
            )
        return None

    def clear_paused(self) -> None:
        """Manual resume foundation (D-15); wired to /resume-budget in 03-02."""
        self._set_paused(False)
        logger.info("BudgetGate: pause cleared (manual resume)")

    def soft_warn_if_needed(self) -> str | None:
        """Optional 80% soft-warn without pausing."""
        cost = self.rolling_cost_24h()
        threshold = self.max_usd * SOFT_WARN_RATIO
        if cost >= threshold and cost < self.max_usd and not self.is_paused():
            return (
                f"⚠️ Budget soft-warn\n"
                f"Coste 24h: ${cost:.4f} / ${self.max_usd:.2f} "
                f"({SOFT_WARN_RATIO:.0%} del tope)\n"
                f"Mutaciones aún permitidas."
            )
        return None
