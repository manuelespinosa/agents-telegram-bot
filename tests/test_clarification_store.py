"""Unit tests for ClarificationStore SQLite TTL (D-04)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pipeline.clarification_store import ClarificationStore


def test_set_get_roundtrip(tmp_path):
    store = ClarificationStore(str(tmp_path / "c.sqlite"), ttl_sec=600)
    store.set(
        chat_id=42,
        user_id=7,
        original_text="arranca la vm",
        question="¿Qué VMID?",
        partial_decision_json='{"intent":"vm_start"}',
    )
    state = store.get(42)
    assert state is not None
    assert state.chat_id == 42
    assert state.user_id == 7
    assert state.original_text == "arranca la vm"
    assert state.question == "¿Qué VMID?"
    assert state.partial_decision_json == '{"intent":"vm_start"}'


def test_expires_after_ttl(tmp_path):
    now = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)
    clock = {"t": now}

    def now_fn():
        return clock["t"]

    store = ClarificationStore(
        str(tmp_path / "c.sqlite"), ttl_sec=600, now_fn=now_fn
    )
    store.set(1, 1, "orig", "q?")
    assert store.get(1) is not None

    clock["t"] = now + timedelta(seconds=601)
    assert store.get(1) is None
    # expired row cleaned
    assert store.get(1) is None


def test_cancel_clears_chat_id(tmp_path):
    store = ClarificationStore(str(tmp_path / "c.sqlite"), ttl_sec=600)
    store.set(99, 1, "orig", "q?")
    assert store.get(99) is not None
    store.cancel(99)
    assert store.get(99) is None


def test_set_overwrites_same_chat(tmp_path):
    store = ClarificationStore(str(tmp_path / "c.sqlite"), ttl_sec=600)
    store.set(5, 1, "first", "q1")
    store.set(5, 2, "second", "q2")
    state = store.get(5)
    assert state is not None
    assert state.original_text == "second"
    assert state.user_id == 2
