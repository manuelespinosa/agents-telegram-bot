"""Unit tests for SysAdminFlow routing with mocked LLMs / agents (no LiteLLM)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from pipeline.clarification_store import ClarificationStore
from pipeline.models import RouterDecision
from pipeline.orchestrator import run_pipeline


def _d(**kwargs) -> RouterDecision:
    base = {
        "intent": "ops",
        "confidence": 0.9,
        "severity": "info",
        "route": "worker",
        "missing_params": [],
        "extracted_params": {},
        "rationale": "test",
    }
    base.update(kwargs)
    return RouterDecision(**base)


def test_worker_path_no_crisis_notify():
    decision = _d(confidence=0.9, severity="info", route="worker", intent="list_vms")

    def classify_fn(msg: str) -> RouterDecision:
        return decision

    def worker_fn(message, decision, **kwargs):
        return "VMs: 100, 300"

    result = run_pipeline(
        "lista las vms",
        actor="telegram:1",
        chat_id=1,
        gate=MagicMock(),
        classify_fn=classify_fn,
        worker_fn=worker_fn,
    )
    assert result.route == "worker"
    assert result.crisis is False
    assert result.deepseek_consulted is False
    assert result.escalate_notify_text is None
    assert "VMs" in result.reply_text


def test_low_confidence_forces_crisis_and_notify_mentions_confidence():
    decision = _d(confidence=0.4, severity="info", route="worker")

    def classify_fn(msg: str) -> RouterDecision:
        return decision

    def crisis_fn(message, decision, **kwargs):
        from pipeline.crisis import build_notify_text

        return build_notify_text(decision, message), "crisis analysis ok"

    result = run_pipeline(
        "algo raro en el lab",
        actor="telegram:1",
        chat_id=1,
        gate=MagicMock(),
        classify_fn=classify_fn,
        crisis_fn=crisis_fn,
    )
    assert result.route == "crisis"
    assert result.crisis is True
    assert result.deepseek_consulted is True
    assert result.escalate_notify_text is not None
    assert "confidence" in result.escalate_notify_text
    assert "0.40" in result.escalate_notify_text or "0.4" in result.escalate_notify_text


def test_keyword_emergencia_forces_crisis_even_if_model_says_worker():
    decision = _d(confidence=0.99, severity="info", route="worker")

    def classify_fn(msg: str) -> RouterDecision:
        return decision

    seen = {}

    def crisis_fn(message, decision, **kwargs):
        from pipeline.crisis import build_notify_text

        seen["msg"] = message
        return build_notify_text(decision, message), "deep analysis"

    result = run_pipeline(
        "emergencia en el cluster",
        actor="telegram:1",
        chat_id=1,
        gate=MagicMock(),
        classify_fn=classify_fn,
        crisis_fn=crisis_fn,
        worker_fn=lambda *a, **k: "should not run",
    )
    assert result.route == "crisis"
    assert result.crisis is True
    assert result.deepseek_consulted is True
    assert "keyword" in (result.escalate_notify_text or "")


def test_missing_params_clarify_populates_store_no_gate_write():
    decision = _d(
        confidence=0.9,
        route="worker",
        missing_params=["vmid"],
        intent="vm_start",
    )
    gate = MagicMock()
    store = ClarificationStore(":memory:", ttl_sec=600)
    # sqlite :memory: is per-connection; use tmp path instead
    import tempfile
    from pathlib import Path

    db = Path(tempfile.mkdtemp()) / "c.sqlite"
    store = ClarificationStore(str(db), ttl_sec=600)

    def classify_fn(msg: str) -> RouterDecision:
        return decision

    result = run_pipeline(
        "arranca la maquina",
        actor="telegram:1",
        chat_id=77,
        gate=gate,
        clarification_store=store,
        user_id=5,
        classify_fn=classify_fn,
        worker_fn=lambda *a, **k: "nope",
    )
    assert result.route == "clarify"
    assert "vmid" in result.reply_text.lower() or "vmid" in result.reply_text
    gate.propose.assert_not_called()
    pending = store.get(77)
    assert pending is not None
    assert pending.original_text == "arranca la maquina"


def test_worker_path_collects_pending_hitl_from_tool_propose():
    decision = _d(
        confidence=0.95,
        intent="vm_start",
        extracted_params={"vmid": 300},
    )

    def classify_fn(msg: str) -> RouterDecision:
        return decision

    def worker_fn(message, decision, **kwargs):
        collector = kwargs.get("pending_collector")
        pr = SimpleNamespace(
            status="pending",
            needs_approval=True,
            message="Approve vm_start 300",
            request_id="hitl-1",
            hitl_request_id="hitl-1",
            action_id="vm_start",
        )
        if collector is not None:
            collector.append(pr)
        return "HITL_PENDING:hitl-1:Approve vm_start 300"

    result = run_pipeline(
        "arranca vm 300",
        actor="telegram:1",
        chat_id=1,
        gate=MagicMock(),
        classify_fn=classify_fn,
        worker_fn=worker_fn,
    )
    assert result.route == "worker"
    assert result.crisis is False
    assert len(result.pending_hitl) == 1
    assert result.pending_hitl[0].request_id == "hitl-1"


def test_crisis_path_sets_deepseek_and_crisis_flags():
    decision = _d(confidence=0.2, severity="critical", route="crisis")

    def classify_fn(msg: str) -> RouterDecision:
        return decision

    def crisis_fn(message, decision, **kwargs):
        return "⚠️ Escalando a Crisis (DeepSeek): motivo=severity:critical", "root cause"

    result = run_pipeline(
        "todo caido",
        actor="telegram:1",
        chat_id=1,
        gate=MagicMock(),
        classify_fn=classify_fn,
        crisis_fn=crisis_fn,
    )
    assert result.crisis is True
    assert result.deepseek_consulted is True
    assert result.route == "crisis"


def test_unparseable_router_fails_safe_to_clarify():
    from pipeline.router import fail_safe_decision, parse_router_decision
    import pytest
    from pydantic import ValidationError

    safe = fail_safe_decision("hola", "bad json")
    assert safe.route == "clarify"
    assert safe.confidence == 0.0
    # no fake missing_params=["clarification"] — UX explains LLM failure instead
    assert safe.missing_params == []
    assert "router_parse_failed" in safe.rationale

    with pytest.raises((ValidationError, ValueError)):
        parse_router_decision("not json at all {{{")

    # orchestrator with classify returning fail-safe
    def classify_fn(msg: str) -> RouterDecision:
        return fail_safe_decision(msg, "boom")

    result = run_pipeline(
        "???",
        actor="telegram:1",
        chat_id=3,
        gate=MagicMock(),
        clarification_store=ClarificationStore(
            str(__import__("tempfile").mkdtemp()) + "/x.sqlite", ttl_sec=600
        ),
        classify_fn=classify_fn,
    )
    assert result.route == "clarify"
    assert result.crisis is False
    assert "router LLM" in result.reply_text or "slash" in result.reply_text.lower()


def test_cancel_clears_pending_clarification(tmp_path):
    store = ClarificationStore(str(tmp_path / "c.sqlite"), ttl_sec=600)
    store.set(9, 1, "arranca algo", "¿VMID?", partial_decision_json=None)

    result = run_pipeline(
        "cancelar",
        actor="telegram:1",
        chat_id=9,
        gate=MagicMock(),
        clarification_store=store,
        classify_fn=lambda m: _d(),  # should not matter
    )
    assert "Cancelado" in result.reply_text
    assert store.get(9) is None


def test_pick_route_ordering():
    from pipeline.flow import pick_route

    # missing params wins over low conf
    d = _d(confidence=0.1, missing_params=["vmid"], route="crisis")
    assert pick_route(d, "urgente") == "clarify"
    d2 = _d(confidence=0.9, route="worker")
    assert pick_route(d2, "lista") == "worker"
    d3 = _d(confidence=0.9, route="worker")
    assert pick_route(d3, "crisis ahora") == "crisis"
