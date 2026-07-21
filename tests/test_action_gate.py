"""ActionGate orchestration unit tests (HITL-04 + core safety)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from action_gate import ActionGate, DecisionResult, ProposeResult
from budget_gate import BudgetGate
from hitl_store import HITLStore
from hmac_lock import canonical_payload, sign_payload


@dataclass
class FakeExecutor:
    reads: list[tuple[str, dict]] = field(default_factory=list)
    writes: list[tuple[str, dict]] = field(default_factory=list)

    def execute_read(self, action_id: str, params: dict[str, Any]) -> str:
        self.reads.append((action_id, params))
        return f"read-ok:{action_id}"

    def execute_write(self, action_id: str, params: dict[str, Any]) -> str:
        self.writes.append((action_id, params))
        return f"write-ok:{action_id}"


class FixedClock:
    def __init__(self, start: datetime | None = None):
        self.now = start or datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now = self.now + timedelta(seconds=seconds)


@pytest.fixture
def gate_bundle(hitl_db_path, cost_db_path, hitl_hmac_secret):
    store = HITLStore(hitl_db_path)
    budget = BudgetGate(cost_db_path=cost_db_path, state_db_path=hitl_db_path)
    executor = FakeExecutor()
    clock = FixedClock()
    gate = ActionGate(
        store=store,
        budget=budget,
        hmac_secret=hitl_hmac_secret,
        executor=executor,
        timeout_sec=300,
        now_fn=clock,
    )
    return {
        "gate": gate,
        "store": store,
        "budget": budget,
        "executor": executor,
        "clock": clock,
        "secret": hitl_hmac_secret,
    }


def test_propose_read_executes_without_hitl(gate_bundle):
    g = gate_bundle
    result = g["gate"].propose("list_vms", {}, reason="scan", actor="agent")
    assert isinstance(result, ProposeResult)
    assert result.status == "completed"
    assert result.needs_approval is False
    assert g["executor"].reads == [("list_vms", {})]
    assert g["executor"].writes == []
    assert g["store"].get(result.request_id or "") is None
    # no pending rows
    assert result.hitl_request_id is None or g["store"].get(result.hitl_request_id) is None


def test_propose_vm_status_read(gate_bundle):
    g = gate_bundle
    result = g["gate"].propose(
        "vm_status", {"vmid": 100}, reason="check", actor="agent"
    )
    assert result.status == "completed"
    assert g["executor"].reads == [("vm_status", {"vmid": 100})]
    assert g["executor"].writes == []


def test_propose_write_creates_pending_hmac_no_execute(gate_bundle):
    g = gate_bundle
    result = g["gate"].propose(
        "vm_start", {"vmid": 100}, reason="boot app", actor="agent"
    )
    assert result.needs_approval is True
    assert result.status == "pending"
    assert result.hitl_request_id
    req = g["store"].get(result.hitl_request_id)
    assert req is not None
    assert req.status == "pending"
    assert req.payload_hmac
    assert req.expires_at
    # ~ now+300s
    exp = datetime.strptime(req.expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    delta = (exp - g["clock"]()).total_seconds()
    assert 299 <= delta <= 301
    assert g["executor"].writes == []
    assert g["executor"].reads == []


def test_approve_verifies_hmac_executes_stored_params(gate_bundle):
    g = gate_bundle
    prop = g["gate"].propose(
        "vm_start", {"vmid": 100}, reason="boot", actor="agent"
    )
    dec = g["gate"].approve(prop.hitl_request_id, user_id=111)
    assert isinstance(dec, DecisionResult)
    assert dec.status == "executed"
    assert g["executor"].writes == [("vm_start", {"vmid": 100})]
    req = g["store"].get(prop.hitl_request_id)
    assert req.status == "executed"


def test_approve_tampered_params_blocks_execute(gate_bundle):
    g = gate_bundle
    prop = g["gate"].propose(
        "vm_stop", {"vmid": 100}, reason="stop", actor="agent"
    )
    rid = prop.hitl_request_id
    # Tamper stored params while keeping old hmac
    with g["store"]._connect() as conn:
        conn.execute(
            "UPDATE hitl_requests SET params_json = ? WHERE request_id = ?",
            (json.dumps({"vmid": 999}), rid),
        )
        conn.commit()
    dec = g["gate"].approve(rid, user_id=111)
    assert dec.status in {"blocked", "failed", "denied"}
    assert g["executor"].writes == []


def test_approve_bad_hmac_blocks_execute(gate_bundle):
    g = gate_bundle
    prop = g["gate"].propose(
        "vm_reboot", {"vmid": 100}, reason="reboot", actor="agent"
    )
    rid = prop.hitl_request_id
    with g["store"]._connect() as conn:
        conn.execute(
            "UPDATE hitl_requests SET payload_hmac = ? WHERE request_id = ?",
            ("deadbeef" * 8, rid),
        )
        conn.commit()
    dec = g["gate"].approve(rid, user_id=111)
    assert dec.status in {"blocked", "failed", "denied"}
    assert g["executor"].writes == []


def test_reject_no_execute(gate_bundle):
    g = gate_bundle
    prop = g["gate"].propose(
        "vm_start", {"vmid": 100}, reason="boot", actor="agent"
    )
    dec = g["gate"].reject(prop.hitl_request_id, user_id=222)
    assert dec.status == "rejected"
    assert g["executor"].writes == []
    assert g["store"].get(prop.hitl_request_id).status == "rejected"


def test_expire_pending_never_executes(gate_bundle):
    g = gate_bundle
    prop = g["gate"].propose(
        "vm_start", {"vmid": 100}, reason="boot", actor="agent"
    )
    ok = g["gate"].expire(prop.hitl_request_id)
    assert ok is True
    assert g["store"].get(prop.hitl_request_id).status == "expired"
    assert g["executor"].writes == []


def test_second_approve_after_executed_noop(gate_bundle):
    g = gate_bundle
    prop = g["gate"].propose(
        "vm_start", {"vmid": 100}, reason="boot", actor="agent"
    )
    first = g["gate"].approve(prop.hitl_request_id, user_id=111)
    assert first.status == "executed"
    second = g["gate"].approve(prop.hitl_request_id, user_id=111)
    assert second.status in {"noop", "already_processed", "denied"}
    assert len(g["executor"].writes) == 1


def test_unknown_action_hard_block_audit(gate_bundle):
    g = gate_bundle
    result = g["gate"].propose(
        "vm_destroy", {"vmid": 100}, reason="nuke", actor="agent"
    )
    assert result.status == "blocked"
    assert result.needs_approval is False
    assert g["executor"].writes == []
    assert g["executor"].reads == []
    events = g["store"].list_audit_events()
    assert any(e["event"] in {"unknown_action", "block"} for e in events)


def test_crisis_sets_requires_deepseek_no_model_call(gate_bundle):
    g = gate_bundle
    result = g["gate"].propose(
        "crisis_probe", {}, reason="incident", actor="agent"
    )
    assert result.needs_approval is True
    assert result.requires_deepseek is True
    assert result.crisis is True
    req = g["store"].get(result.hitl_request_id)
    assert req.requires_deepseek is True
    assert req.risk_tier == "crisis"
    # No LLM/DeepSeek invocations — FakeExecutor has no model methods; ensure no writes
    assert g["executor"].writes == []
    assert g["executor"].reads == []


def test_budget_paused_blocks_write_allows_read(gate_bundle):
    g = gate_bundle
    g["budget"]._set_paused(True, reason="budget_exceeded")
    write_res = g["gate"].propose(
        "vm_start", {"vmid": 100}, reason="boot", actor="agent"
    )
    assert write_res.status == "blocked"
    assert write_res.needs_approval is False
    assert g["executor"].writes == []
    read_res = g["gate"].propose("list_vms", {}, reason="scan", actor="agent")
    assert read_res.status == "completed"
    assert g["executor"].reads == [("list_vms", {})]


def test_no_remembered_approval_two_distinct_pending(gate_bundle):
    g = gate_bundle
    a = g["gate"].propose("vm_start", {"vmid": 100}, reason="a", actor="agent")
    b = g["gate"].propose("vm_start", {"vmid": 100}, reason="b", actor="agent")
    assert a.hitl_request_id != b.hitl_request_id
    assert g["store"].get(a.hitl_request_id).status == "pending"
    assert g["store"].get(b.hitl_request_id).status == "pending"


def test_approve_expired_request_denies(gate_bundle):
    g = gate_bundle
    prop = g["gate"].propose(
        "vm_start", {"vmid": 100}, reason="boot", actor="agent"
    )
    g["clock"].advance(301)
    dec = g["gate"].approve(prop.hitl_request_id, user_id=111)
    assert dec.status in {"expired", "denied"}
    assert g["executor"].writes == []
    assert g["store"].get(prop.hitl_request_id).status == "expired"


def test_propose_snapshot_create_pending_hitl_no_execute(gate_bundle):
    """Phase 4: snapshot_create is write-tier — pending HITL until approve (D-05)."""
    g = gate_bundle
    result = g["gate"].propose(
        "snapshot_create",
        {"vmid": 300, "snapname": "ai-e2e", "description": "phase4"},
        reason="backup before change",
        actor="agent",
    )
    assert result.needs_approval is True
    assert result.status == "pending"
    assert result.hitl_request_id
    req = g["store"].get(result.hitl_request_id)
    assert req is not None
    assert req.status == "pending"
    assert req.risk_tier == "write"
    assert g["executor"].writes == []
    assert g["executor"].reads == []

    dec = g["gate"].approve(result.hitl_request_id, user_id=111)
    assert dec.status == "executed"
    assert g["executor"].writes == [
        (
            "snapshot_create",
            {"vmid": 300, "snapname": "ai-e2e", "description": "phase4"},
        )
    ]


def test_propose_snapshot_list_read_no_hitl(gate_bundle):
    g = gate_bundle
    result = g["gate"].propose(
        "snapshot_list", {"vmid": 300}, reason="inspect", actor="agent"
    )
    assert result.status == "completed"
    assert result.needs_approval is False
    assert g["executor"].reads == [("snapshot_list", {"vmid": 300})]
    assert g["executor"].writes == []


def test_no_telegram_imports_in_action_gate():
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "action_gate.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "telegram" not in imported
    assert "telegram.ext" not in imported
