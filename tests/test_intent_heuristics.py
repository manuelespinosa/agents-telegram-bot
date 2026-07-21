"""Deterministic list_vms heuristics (typo-tolerant, no LLM)."""
from __future__ import annotations

from pipeline.intent_heuristics import is_list_vms_request, try_deterministic_decision
from pipeline.router import classify, fail_safe_decision
from pipeline.worker import run_worker


def test_list_vms_spanish_typo():
    assert is_list_vms_request("lsita las VMs") is True
    d = try_deterministic_decision("lsita las VMs")
    assert d is not None
    assert d.intent == "list_vms"
    assert d.route == "worker"
    assert d.missing_params == []
    assert d.extracted_params.get("skip_llm") is True


def test_list_vms_variants():
    for msg in (
        "lista las VMs",
        "listar maquinas",
        "list vms",
        "muestra los CT",
        "inventario del cluster",
    ):
        assert is_list_vms_request(msg), msg


def test_write_with_vmid_heuristic():
    d = try_deterministic_decision("reinicia la VM 300")
    assert d is not None
    assert d.intent == "vm_reboot"
    assert d.route == "worker"
    assert d.extracted_params.get("vmid") == 300
    assert d.extracted_params.get("skip_llm") is True


def test_write_missing_vmid_clarify():
    d = try_deterministic_decision("apaga la maquina por favor")
    assert d is not None
    assert d.route == "clarify"
    assert d.missing_params == ["vmid"]


def test_not_list_vms_write_still_detected_as_write():
    assert is_list_vms_request("reinicia la VM 300") is False
    assert try_deterministic_decision("snapshot de la vm 300") is not None


def test_classify_prefers_heuristic_without_llm():
    d = classify("lista las VMs", llm_call=lambda _p: (_ for _ in ()).throw(RuntimeError("no llm")))
    assert d.intent == "list_vms"
    assert d.rationale.startswith("heuristic:")


def test_fail_safe_no_fake_clarification_param():
    d = fail_safe_decision("hola", "boom")
    assert d.missing_params == []
    assert d.route == "clarify"


def test_worker_skip_llm_calls_gate(monkeypatch):
    class FakeGate:
        def propose(self, action_id, params, reason="", actor=""):
            assert action_id == "list_vms"
            class R:
                execution_result = "vm 100 running"
                result = None
                message = "ok"
                needs_approval = False
                status = "ok"
            return R()

    from pipeline.models import RouterDecision

    d = RouterDecision(
        intent="list_vms",
        confidence=0.95,
        route="worker",
        extracted_params={"skip_llm": True},
        rationale="heuristic:list_vms",
    )
    text = run_worker("lista las VMs", d, gate=FakeGate(), actor="test")
    assert "vm 100" in text


def test_worker_skip_llm_write_collects_pending():
    class FakeGate:
        def propose(self, action_id, params, reason="", actor=""):
            assert action_id == "vm_reboot"
            assert params["vmid"] == 300
            class R:
                execution_result = None
                result = None
                message = "pending approve"
                needs_approval = True
                status = "pending"
                request_id = "abc"
            return R()

    from pipeline.models import RouterDecision

    pending = []
    d = RouterDecision(
        intent="vm_reboot",
        confidence=0.92,
        route="worker",
        extracted_params={"skip_llm": True, "vmid": 300, "action_id": "vm_reboot"},
    )
    text = run_worker(
        "reinicia vm 300", d, gate=FakeGate(), actor="test", pending_collector=pending
    )
    assert pending
    assert "aprobación" in text.lower() or "pending" in text.lower()
