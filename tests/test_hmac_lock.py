"""HITL-03: HMAC-SHA256 lock unit tests."""
from __future__ import annotations

import hmac as hmac_mod

import hmac_lock


def test_sign_then_verify_succeeds(hitl_hmac_secret):
    payload = hmac_lock.canonical_payload(
        action_id="vm_start",
        params={"vmid": 100},
        request_id="abc123",
        expires_at="2026-07-21T12:00:00Z",
    )
    sig = hmac_lock.sign_payload(hitl_hmac_secret, payload)
    assert hmac_lock.verify_payload(hitl_hmac_secret, payload, sig) is True


def test_tampered_params_fail_verify(hitl_hmac_secret):
    expires = "2026-07-21T12:00:00Z"
    rid = "req-1"
    original = hmac_lock.canonical_payload(
        "vm_start", {"vmid": 100}, rid, expires
    )
    sig = hmac_lock.sign_payload(hitl_hmac_secret, original)
    tampered = hmac_lock.canonical_payload(
        "vm_start", {"vmid": 999}, rid, expires
    )
    assert hmac_lock.verify_payload(hitl_hmac_secret, tampered, sig) is False


def test_tampered_action_id_fail_verify(hitl_hmac_secret):
    expires = "2026-07-21T12:00:00Z"
    rid = "req-2"
    original = hmac_lock.canonical_payload(
        "vm_start", {"vmid": 100}, rid, expires
    )
    sig = hmac_lock.sign_payload(hitl_hmac_secret, original)
    tampered = hmac_lock.canonical_payload(
        "vm_stop", {"vmid": 100}, rid, expires
    )
    assert hmac_lock.verify_payload(hitl_hmac_secret, tampered, sig) is False


def test_wrong_secret_fails_verify(hitl_hmac_secret):
    payload = hmac_lock.canonical_payload(
        "vm_reboot", {"vmid": 100}, "req-3", "2026-07-21T12:00:00Z"
    )
    sig = hmac_lock.sign_payload(hitl_hmac_secret, payload)
    wrong = b"x" * 32
    assert hmac_lock.verify_payload(wrong, payload, sig) is False


def test_verify_uses_compare_digest(monkeypatch, hitl_hmac_secret):
    """Ensure timing-safe compare path is used (not raw ==)."""
    called = {"n": 0}
    real_compare = hmac_mod.compare_digest

    def tracking_compare(a, b):
        called["n"] += 1
        return real_compare(a, b)

    monkeypatch.setattr(hmac_mod, "compare_digest", tracking_compare)
    # hmac_lock must import compare_digest from hmac; re-bind module attribute
    monkeypatch.setattr(hmac_lock.hmac, "compare_digest", tracking_compare)

    payload = hmac_lock.canonical_payload(
        "list_vms", {}, "req-4", "2026-07-21T12:00:00Z"
    )
    sig = hmac_lock.sign_payload(hitl_hmac_secret, payload)
    assert hmac_lock.verify_payload(hitl_hmac_secret, payload, sig) is True
    assert called["n"] >= 1


def test_canonical_payload_is_sorted_compact_utf8():
    payload = hmac_lock.canonical_payload(
        "vm_status",
        {"node": "pve", "vmid": 100},
        "rid",
        "exp",
    )
    text = payload.decode("utf-8")
    # compact separators, sorted keys
    assert " " not in text
    assert text.index("action_id") < text.index("expires_at")
    assert text.index("expires_at") < text.index("params")
    assert text.index("params") < text.index("request_id")
