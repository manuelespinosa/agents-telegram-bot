"""HITL store queue + single-use transitions unit tests."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from hitl_store import HITLStore


def _expires_iso(minutes: int = 5) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def test_create_request_persists_pending(hitl_db_path):
    store = HITLStore(hitl_db_path)
    req = store.create_request(
        request_id="rid-1",
        action_id="vm_start",
        params={"vmid": 100},
        risk_tier="write",
        requires_deepseek=False,
        reason="test start",
        payload_canonical='{"action_id":"vm_start"}',
        payload_hmac="abc",
        expires_at=_expires_iso(),
    )
    assert req.status == "pending"
    loaded = store.get("rid-1")
    assert loaded is not None
    assert loaded.action_id == "vm_start"
    assert json.loads(loaded.params_json) == {"vmid": 100}
    assert loaded.payload_hmac == "abc"
    assert loaded.expires_at
    assert loaded.status == "pending"


def test_expire_if_pending_once(hitl_db_path):
    store = HITLStore(hitl_db_path)
    store.create_request(
        request_id="rid-exp",
        action_id="vm_stop",
        params={"vmid": 101},
        risk_tier="write",
        requires_deepseek=False,
        reason="stop",
        payload_canonical="{}",
        payload_hmac="h",
        expires_at=_expires_iso(),
    )
    assert store.expire_if_pending("rid-exp") is True
    assert store.get("rid-exp").status == "expired"
    assert store.expire_if_pending("rid-exp") is False
    # never implies execute
    assert store.get("rid-exp").status != "executed"


def test_mark_approved_only_from_pending(hitl_db_path):
    store = HITLStore(hitl_db_path)
    store.create_request(
        request_id="rid-ap",
        action_id="vm_reboot",
        params={"vmid": 102},
        risk_tier="write",
        requires_deepseek=False,
        reason="reboot",
        payload_canonical="{}",
        payload_hmac="h",
        expires_at=_expires_iso(),
    )
    assert store.mark_approved("rid-ap", decided_by="111") is True
    assert store.get("rid-ap").status == "approved"
    # second approve fails closed
    assert store.mark_approved("rid-ap", decided_by="111") is False
    assert store.get("rid-ap").status == "approved"


def test_mark_rejected_terminal(hitl_db_path):
    store = HITLStore(hitl_db_path)
    store.create_request(
        request_id="rid-rej",
        action_id="vm_start",
        params={"vmid": 100},
        risk_tier="write",
        requires_deepseek=False,
        reason="r",
        payload_canonical="{}",
        payload_hmac="h",
        expires_at=_expires_iso(),
    )
    assert store.mark_rejected("rid-rej", decided_by="222") is True
    assert store.get("rid-rej").status == "rejected"
    assert store.mark_approved("rid-rej", decided_by="222") is False
    assert store.mark_executed("rid-rej", result="nope") is False


def test_mark_executed_cannot_reexecute(hitl_db_path):
    store = HITLStore(hitl_db_path)
    store.create_request(
        request_id="rid-ex",
        action_id="vm_start",
        params={"vmid": 100},
        risk_tier="write",
        requires_deepseek=False,
        reason="r",
        payload_canonical="{}",
        payload_hmac="h",
        expires_at=_expires_iso(),
    )
    assert store.mark_approved("rid-ex", decided_by="111") is True
    assert store.mark_executed("rid-ex", result="ok") is True
    assert store.get("rid-ex").status == "executed"
    assert store.mark_executed("rid-ex", result="again") is False


def test_mark_failed_terminal(hitl_db_path):
    store = HITLStore(hitl_db_path)
    store.create_request(
        request_id="rid-fail",
        action_id="vm_start",
        params={"vmid": 100},
        risk_tier="write",
        requires_deepseek=False,
        reason="r",
        payload_canonical="{}",
        payload_hmac="h",
        expires_at=_expires_iso(),
    )
    store.mark_approved("rid-fail", decided_by="111")
    assert store.mark_failed("rid-fail", result="boom") is True
    assert store.get("rid-fail").status == "failed"
    assert store.mark_executed("rid-fail", result="late") is False


def test_audit_log_records_events(hitl_db_path):
    store = HITLStore(hitl_db_path)
    store.audit("unknown_action", {"action_id": "vm_destroy"})
    store.audit("expire", {"request_id": "x"})
    store.audit("block", {"reason": "budget"})
    events = store.list_audit_events()
    kinds = {e["event"] for e in events}
    assert "unknown_action" in kinds
    assert "expire" in kinds
    assert "block" in kinds
