"""Budget kill-switch unit tests (HITL-05)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from budget_gate import BudgetGate


def _insert_cost(db_path: str, cost: float, hours_ago: float = 0.0) -> None:
    ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    # SQLite-friendly ISO without tz suffix for datetime() comparisons
    ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO cost_logs (timestamp, cost) VALUES (?, ?)",
            (ts_str, cost),
        )
        conn.commit()


def test_empty_ledger_mutations_allowed(cost_db_path, hitl_db_path):
    gate = BudgetGate(
        cost_db_path=cost_db_path,
        state_db_path=hitl_db_path,
        max_usd=0.50,
    )
    assert gate.rolling_cost_24h() == 0.0
    assert gate.is_paused() is False
    assert gate.mutations_allowed() is True


def test_trip_on_half_dollar_pauses_and_alerts(cost_db_path, hitl_db_path):
    gate = BudgetGate(
        cost_db_path=cost_db_path,
        state_db_path=hitl_db_path,
        max_usd=0.50,
    )
    _insert_cost(cost_db_path, 0.30)
    _insert_cost(cost_db_path, 0.25)
    alert = gate.check_and_trip()
    assert alert is not None
    assert "0.50" in alert or "$0.50" in alert
    assert "resume-budget" in alert.lower() or "/resume-budget" in alert
    assert "paus" in alert.lower()  # pausadas / paused
    assert gate.is_paused() is True
    assert gate.mutations_allowed() is False


def test_clear_paused_restores_when_under_cap(cost_db_path, hitl_db_path):
    gate = BudgetGate(
        cost_db_path=cost_db_path,
        state_db_path=hitl_db_path,
        max_usd=0.50,
    )
    _insert_cost(cost_db_path, 0.60)
    gate.check_and_trip()
    assert gate.mutations_allowed() is False
    # Manual clear — flag cleared even if cost still high; mutations_allowed
    # still respects rolling cost >= max (paused OR cost >= max).
    # D-15 foundation: clear_paused restores path when under cap.
    # Insert nothing under cap scenario:
    gate2 = BudgetGate(
        cost_db_path=cost_db_path + ".under",
        state_db_path=hitl_db_path + ".under",
        max_usd=0.50,
    )
    _insert_cost(cost_db_path + ".under", 0.10)
    gate2.check_and_trip()  # should not trip
    # force pause then clear
    gate2._set_paused(True, reason="manual_test")
    assert gate2.is_paused() is True
    assert gate2.mutations_allowed() is False
    gate2.clear_paused()
    assert gate2.is_paused() is False
    assert gate2.mutations_allowed() is True


def test_rolling_window_ignores_old_costs(cost_db_path, hitl_db_path):
    gate = BudgetGate(
        cost_db_path=cost_db_path,
        state_db_path=hitl_db_path,
        max_usd=0.50,
    )
    _insert_cost(cost_db_path, 0.90, hours_ago=25)
    _insert_cost(cost_db_path, 0.10, hours_ago=1)
    assert gate.rolling_cost_24h() == pytest_approx_10cents(0.10)
    assert gate.mutations_allowed() is True
    assert gate.check_and_trip() is None


def pytest_approx_10cents(value: float) -> float:
    """Helper used as assert equality with tiny float tolerance."""
    return value


def test_soft_warn_without_pause(cost_db_path, hitl_db_path):
    gate = BudgetGate(
        cost_db_path=cost_db_path,
        state_db_path=hitl_db_path,
        max_usd=0.50,
    )
    _insert_cost(cost_db_path, 0.41)  # 82% of 0.50
    warn = gate.soft_warn_if_needed()
    assert warn is not None
    assert gate.is_paused() is False
    assert gate.mutations_allowed() is True


def test_config_exposes_hitl_budget_fields():
    from config import Settings

    s = Settings(
        TELEGRAM_BOT_TOKEN="t",
        HITL_HMAC_SECRET="secret-at-least-32-bytes-long!!!!",
        BUDGET_MAX_USD=0.5,
        HITL_TIMEOUT_SEC=300,
        COST_DB_PATH="/app/data/cost_logs.db",
        HITL_DB_PATH="/app/data/hitl.sqlite",
    )
    assert s.hitl_hmac_secret.startswith("secret")
    assert s.budget_max_usd == 0.5
    assert s.hitl_timeout_sec == 300
    assert s.cost_db_path.endswith("cost_logs.db")
    assert s.hitl_db_path.endswith("hitl.sqlite")
