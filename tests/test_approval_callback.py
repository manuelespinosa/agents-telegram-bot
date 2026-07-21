"""Telegram HITL handler wiring unit tests — mocked Update/CallbackQuery."""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from action_gate import ActionGate, DecisionResult, ProposeResult
from budget_gate import BudgetGate
from handlers import (
    approval_callback,
    approval_keyboard,
    cmd_resume_budget,
    format_approval_message,
    require_authorized,
)
from hitl_store import HITLStore


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


@pytest.fixture
def hitl_secret() -> bytes:
    return b"unit-test-hitl-hmac-secret-32b!!"


def _make_context(
    *,
    gate: Any = None,
    budget: Any = None,
    store: Any = None,
    writes_enabled: bool = True,
    allowed_patch=None,
) -> MagicMock:
    context = MagicMock()
    bot_data = {
        "action_gate": gate,
        "budget_gate": budget,
        "hitl_store": store,
        "hitl_writes_enabled": writes_enabled,
        "chat_store": None,
    }
    context.application.bot_data = bot_data
    context.application.job_queue = None
    context.bot = MagicMock()
    context.bot.edit_message_text = AsyncMock()
    return context


def _callback_update(user_id: int, data: str) -> MagicMock:
    update = MagicMock()
    update.effective_user = SimpleNamespace(id=user_id)
    update.effective_chat = SimpleNamespace(id=999)
    update.message = None
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update.callback_query = query
    return update


def _message_update(user_id: int) -> MagicMock:
    update = MagicMock()
    update.effective_user = SimpleNamespace(id=user_id)
    update.effective_chat = SimpleNamespace(id=999)
    update.callback_query = None
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    return update


@pytest.fixture
def authorized_settings(monkeypatch):
    from config import Settings

    s = Settings(
        TELEGRAM_BOT_TOKEN="t",
        TELEGRAM_ALLOWED_USERS="111,222",
        HITL_HMAC_SECRET="unit-test-hitl-hmac-secret-32b!!",
    )
    monkeypatch.setattr("handlers.settings", s)
    monkeypatch.setattr("config.settings", s)
    return s


@pytest.mark.asyncio
async def test_approval_callback_unauthorized_denied(authorized_settings, hitl_db_path, cost_db_path, hitl_secret):
    store = HITLStore(hitl_db_path)
    budget = BudgetGate(cost_db_path=cost_db_path, state_db_path=hitl_db_path)
    executor = FakeExecutor()
    gate = ActionGate(store, budget, hitl_secret, executor, timeout_sec=300)
    context = _make_context(gate=gate, budget=budget, store=store)
    update = _callback_update(user_id=99999, data="approve:deadbeef")

    await approval_callback(update, context)

    update.callback_query.answer.assert_awaited()
    # Unauthorized: answer with alert; no edit with execution
    call_kwargs = update.callback_query.answer.await_args
    assert call_kwargs is not None
    # gate.approve not called — no pending anyway; ensure no writes
    assert executor.writes == []
    update.callback_query.edit_message_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_approval_callback_approve_calls_gate(
    authorized_settings, hitl_db_path, cost_db_path, hitl_secret
):
    store = HITLStore(hitl_db_path)
    budget = BudgetGate(cost_db_path=cost_db_path, state_db_path=hitl_db_path)
    executor = FakeExecutor()
    gate = ActionGate(store, budget, hitl_secret, executor, timeout_sec=300)
    proposed = gate.propose(
        "vm_start", {"vmid": 300}, reason="test boot", actor="test"
    )
    assert proposed.needs_approval
    rid = proposed.request_id
    assert rid

    context = _make_context(gate=gate, budget=budget, store=store)
    update = _callback_update(user_id=111, data=f"approve:{rid}")

    await approval_callback(update, context)

    assert executor.writes == [("vm_start", {"vmid": 300})]
    update.callback_query.edit_message_text.assert_awaited()
    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "executed" in text.lower() or "✅" in text
    # plain text only — no parse_mode
    kwargs = update.callback_query.edit_message_text.await_args.kwargs
    assert kwargs.get("parse_mode") is None


@pytest.mark.asyncio
async def test_approval_callback_reject_no_write(
    authorized_settings, hitl_db_path, cost_db_path, hitl_secret
):
    store = HITLStore(hitl_db_path)
    budget = BudgetGate(cost_db_path=cost_db_path, state_db_path=hitl_db_path)
    executor = FakeExecutor()
    gate = ActionGate(store, budget, hitl_secret, executor, timeout_sec=300)
    proposed = gate.propose(
        "vm_stop", {"vmid": 300}, reason="test stop", actor="test"
    )
    rid = proposed.request_id

    context = _make_context(gate=gate, budget=budget, store=store)
    update = _callback_update(user_id=111, data=f"reject:{rid}")

    await approval_callback(update, context)

    assert executor.writes == []
    req = store.get(rid)
    assert req is not None
    assert req.status == "rejected"
    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "❌" in text or "reject" in text.lower() or "Deneg" in text or "no ejecución" in text.lower() or "Sin ejecución" in text


def test_approval_keyboard_callback_data_under_64():
    rid = "a" * 32  # uuid4 hex length
    kb = approval_keyboard(rid)
    row = kb.inline_keyboard[0]
    assert len(row) == 2
    assert row[0].callback_data == f"approve:{rid}"
    assert row[1].callback_data == f"reject:{rid}"
    assert len(row[0].callback_data.encode("utf-8")) < 64
    assert len(row[1].callback_data.encode("utf-8")) < 64
    assert "Aprobar" in row[0].text
    assert "Denegar" in row[1].text
    # No Modify button
    assert all("Modif" not in b.text for b in row)


def test_format_approval_message_includes_d06_fields():
    text = format_approval_message(
        action_id="vm_start",
        target="VM 300",
        tier="write",
        impact="VM powers on",
        reason="boot lab",
        expires_at="2026-07-21T12:05:00Z",
        crisis=False,
        deepseek_consulted=False,
    )
    assert "vm_start" in text
    assert "VM 300" in text or "300" in text
    assert "write" in text
    assert "powers on" in text or "Impacto" in text
    assert "boot lab" in text
    assert "5 min" in text or "Caduca" in text
    assert "Aprobación" in text
    assert "stub Phase 3" not in text


def test_format_approval_message_crisis_badge_d11():
    text = format_approval_message(
        action_id="vm_reboot",
        target="VM 300",
        tier="write",
        impact="Reboots guest",
        reason="crisis analysis",
        expires_at="2026-07-21T12:05:00Z",
        crisis=True,
        deepseek_consulted=True,
    )
    assert "CRISIS" in text
    assert "DeepSeek consultado" in text
    assert "stub Phase 3" not in text
    assert "sin invocación real" not in text
    # legacy kw still accepted, no stub wording
    legacy = format_approval_message(
        action_id="crisis_probe",
        target="cluster",
        tier="crisis",
        impact="none",
        reason="legacy",
        expires_at="2026-07-21T12:05:00Z",
        crisis_stub=True,
    )
    assert "DeepSeek" in legacy
    assert "stub Phase 3" not in legacy


@pytest.mark.asyncio
async def test_resume_budget_clears_pause(
    authorized_settings, hitl_db_path, cost_db_path
):
    budget = BudgetGate(cost_db_path=cost_db_path, state_db_path=hitl_db_path)
    budget._set_paused(True, reason="test")
    assert budget.is_paused()

    context = _make_context(budget=budget)
    update = _message_update(user_id=111)

    await cmd_resume_budget(update, context)

    assert not budget.is_paused()
    update.message.reply_text.assert_awaited()
    text = update.message.reply_text.await_args.args[0]
    assert "clear_paused" in text or "Kill-switch" in text or "✅" in text


@pytest.mark.asyncio
async def test_resume_budget_unauthorized(authorized_settings, cost_db_path, hitl_db_path):
    budget = BudgetGate(cost_db_path=cost_db_path, state_db_path=hitl_db_path)
    budget._set_paused(True, reason="test")
    context = _make_context(budget=budget)
    update = _message_update(user_id=99999)

    await cmd_resume_budget(update, context)

    assert budget.is_paused()  # not cleared
    update.message.reply_text.assert_awaited()
    assert "autorizado" in update.message.reply_text.await_args.args[0].lower()
