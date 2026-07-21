"""Cost logger: real USD extraction + BudgetGate ledger wiring."""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock

from budget_gate import BudgetGate
from pipeline.cost_logger import (
    cost_db_scope,
    estimate_cost,
    log_usage,
    record_usage,
    usage_from_crewai_result,
    usage_from_openai_response,
)
from pipeline.models import RouterDecision
from pipeline.orchestrator import run_pipeline


def test_estimate_cost_gemini_flash_nonzero():
    cost = estimate_cost("gemini-flash", tokens_in=1_000_000, tokens_out=0)
    assert abs(cost - 0.10) < 1e-9


def test_usage_from_openai_prefers_provider_cost():
    data = {
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "cost": 0.00123,
        }
    }
    m = usage_from_openai_response(data, model="openai/gemini-flash")
    assert m.model == "gemini-flash"
    assert m.tokens_in == 100
    assert m.tokens_out == 50
    assert abs(m.cost - 0.00123) < 1e-9
    assert m.cost_source == "provider"


def test_usage_from_openai_estimates_when_no_cost():
    data = {
        "usage": {
            "prompt_tokens": 1_000_000,
            "completion_tokens": 0,
        }
    }
    m = usage_from_openai_response(data, model="gemini-flash")
    assert m.cost_source == "estimate"
    assert abs(m.cost - 0.10) < 1e-9


def test_usage_from_hidden_params_response_cost():
    data = {
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        "_hidden_params": {"response_cost": 0.00042},
    }
    m = usage_from_openai_response(data, model="qwen-coder")
    assert m.cost_source == "provider"
    assert abs(m.cost - 0.00042) < 1e-9


def test_usage_from_crewai_result_dict():
    result = SimpleNamespace(
        token_usage={
            "prompt_tokens": 200,
            "completion_tokens": 100,
            "total_cost": 0.002,
        }
    )
    m = usage_from_crewai_result(result, model="qwen-coder")
    assert m.tokens_in == 200
    assert m.tokens_out == 100
    assert abs(m.cost - 0.002) < 1e-9
    assert m.cost_source == "provider"


def test_record_usage_writes_nonzero_and_trips_budget(cost_db_path, hitl_db_path):
    gate = BudgetGate(
        cost_db_path=cost_db_path,
        state_db_path=hitl_db_path,
        max_usd=0.50,
    )
    assert gate.mutations_allowed() is True

    with cost_db_scope(cost_db_path):
        record_usage(
            model="gemini-flash",
            tokens_in=100,
            tokens_out=50,
            cost=0.30,
        )
        record_usage(
            model="qwen-coder",
            tokens_in=100,
            tokens_out=50,
            cost=0.25,
        )

    assert abs(gate.rolling_cost_24h() - 0.55) < 1e-9
    alert = gate.check_and_trip()
    assert alert is not None
    assert gate.mutations_allowed() is False


def test_record_usage_skips_empty_zero_rows(cost_db_path):
    with cost_db_scope(cost_db_path):
        record_usage(model="gemini-flash", tokens_in=0, tokens_out=0, cost=0.0)
    with sqlite3.connect(cost_db_path) as conn:
        try:
            n = conn.execute("SELECT COUNT(*) FROM cost_logs").fetchone()[0]
        except sqlite3.OperationalError:
            n = 0
    assert n == 0


def test_log_usage_direct_insert(cost_db_path):
    log_usage(
        cost_db_path,
        model="deepseek-r1",
        tokens_in=10,
        tokens_out=20,
        cost=0.01,
    )
    with sqlite3.connect(cost_db_path) as conn:
        row = conn.execute(
            "SELECT cost, model, tokens_in, tokens_out FROM cost_logs"
        ).fetchone()
    assert row[0] == 0.01
    assert row[1] == "deepseek-r1"
    assert row[2] == 10
    assert row[3] == 20


def test_orchestrator_no_longer_writes_zero_placeholder(cost_db_path, hitl_db_path):
    """Regression: old code always inserted cost=0.0 after every pipeline run."""
    decision = RouterDecision(
        intent="list_vms",
        confidence=0.9,
        severity="info",
        route="worker",
        missing_params=[],
        extracted_params={},
        rationale="test",
    )
    budget = BudgetGate(
        cost_db_path=cost_db_path,
        state_db_path=hitl_db_path,
        max_usd=0.50,
    )

    result = run_pipeline(
        "lista las vms",
        actor="telegram:1",
        chat_id=1,
        gate=MagicMock(),
        cost_db_path=cost_db_path,
        budget=budget,
        classify_fn=lambda msg: decision,
        worker_fn=lambda *a, **k: "VMs: 100",
    )
    assert result.route == "worker"

    with sqlite3.connect(cost_db_path) as conn:
        # Schema may not exist if nothing was logged — either 0 rows or no zero-only noise
        try:
            rows = conn.execute(
                "SELECT cost, tokens_in, tokens_out FROM cost_logs"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
    # Heuristic path: no LLM → no dummy zero row
    assert rows == []


def test_router_http_records_usage_from_mock_response(cost_db_path, monkeypatch):
    """chat_json must record provider cost into active cost_db_scope."""
    import httpx as real_httpx
    import pipeline.router_http as rh

    class _Resp:
        status_code = 200
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"intent":"list_vms","confidence":0.9,'
                                '"severity":"info","route":"worker",'
                                '"missing_params":[],"extracted_params":{},'
                                '"rationale":"ok"}'
                            )
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 40,
                    "cost": 0.0007,
                },
            }

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            return _Resp()

    # chat_json does `import httpx` locally — patch the real module Client
    monkeypatch.setattr(real_httpx, "Client", _Client)

    with cost_db_scope(cost_db_path):
        content = rh.chat_json(
            system="sys",
            user="lista vms",
            model="gemini-flash",
        )
    assert "list_vms" in content or "intent" in content

    with sqlite3.connect(cost_db_path) as conn:
        row = conn.execute(
            "SELECT cost, tokens_in, tokens_out, model FROM cost_logs"
        ).fetchone()
    assert row is not None
    assert abs(row[0] - 0.0007) < 1e-9
    assert row[1] == 120
    assert row[2] == 40
    assert row[3] == "gemini-flash"
