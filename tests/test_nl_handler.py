"""NL MessageHandler unit tests — pipeline mock, auth, slash isolation (D-01/D-02/D-12)."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from action_gate import ProposeResult
from handlers import cmd_list_vms, format_approval_message, nl_message_handler
from pipeline.models import PipelineResult


def _make_context(
    *,
    gate: Any = None,
    budget: Any = None,
    store: Any = None,
    clarification_store: Any = None,
    writes_enabled: bool = True,
    pipeline_enabled: bool = True,
    cost_db_path: str = "/tmp/cost_test.db",
) -> MagicMock:
    context = MagicMock()
    bot_data = {
        "action_gate": gate,
        "budget_gate": budget,
        "hitl_store": store,
        "hitl_writes_enabled": writes_enabled,
        "chat_store": None,
        "clarification_store": clarification_store,
        "pipeline_enabled": pipeline_enabled,
        "cost_db_path": cost_db_path,
    }
    context.application.bot_data = bot_data
    context.application.job_queue = None
    context.bot = MagicMock()
    return context


def _message_update(user_id: int, text: str = "lista las VMs") -> MagicMock:
    update = MagicMock()
    update.effective_user = SimpleNamespace(id=user_id)
    update.effective_chat = SimpleNamespace(id=999)
    update.callback_query = None
    update.message = MagicMock()
    update.message.text = text
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


def test_format_approval_message_crisis_badge_no_phase3_stub():
    text = format_approval_message(
        action_id="snapshot_create",
        target="VM 300",
        tier="write",
        impact="Creates snapshot",
        reason="ai e2e",
        expires_at="2026-07-21T12:05:00Z",
        crisis=True,
        deepseek_consulted=True,
    )
    assert "CRISIS" in text or "Crisis" in text
    assert "DeepSeek consultado" in text or "DeepSeek" in text
    assert "stub Phase 3" not in text
    assert "sin invocación real" not in text
    assert "Aprobación" in text


@pytest.mark.asyncio
async def test_nl_unauthorized_does_not_call_pipeline(authorized_settings):
    gate = MagicMock()
    context = _make_context(gate=gate)
    update = _message_update(user_id=99999, text="lista las VMs")

    with patch("handlers.run_pipeline") as mock_pipe:
        await nl_message_handler(update, context)
        mock_pipe.assert_not_called()

    update.message.reply_text.assert_awaited()
    assert "autorizado" in update.message.reply_text.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_nl_authorized_calls_pipeline_and_sends_escalate_then_reply(
    authorized_settings,
):
    gate = MagicMock()
    context = _make_context(gate=gate)
    update = _message_update(user_id=111, text="urgente caida cluster")

    pipe_result = PipelineResult(
        reply_text="Análisis: nodos OK.",
        escalate_notify_text="⚠️ Escalando a Crisis (DeepSeek): motivo=keyword:urgente",
        pending_hitl=[],
        crisis=True,
        deepseek_consulted=True,
        route="crisis",
    )

    with patch("handlers.run_pipeline", return_value=pipe_result) as mock_pipe:
        await nl_message_handler(update, context)
        mock_pipe.assert_called_once()
        kwargs = mock_pipe.call_args.kwargs
        assert kwargs["actor"] == "telegram:111"
        assert kwargs["chat_id"] == 999
        assert kwargs["gate"] is gate

    # First reply = escalate notify (D-12), then final analysis
    calls = [c.args[0] for c in update.message.reply_text.await_args_list]
    assert any("Escalando a Crisis" in c for c in calls)
    assert any("Análisis" in c or "nodos" in c for c in calls)
    # plain text only
    for c in update.message.reply_text.await_args_list:
        assert c.kwargs.get("parse_mode") is None


@pytest.mark.asyncio
async def test_nl_pending_hitl_sends_approval_keyboard(authorized_settings):
    gate = MagicMock()
    store = MagicMock()
    context = _make_context(gate=gate, store=store)
    update = _message_update(user_id=111, text="crea snapshot en VM 300")

    pending = ProposeResult(
        status="pending",
        needs_approval=True,
        message="raw",
        request_id="abc123def456",
        hitl_request_id="abc123def456",
        action_id="snapshot_create",
        tier="write",
        target="VM 300",
        expected_impact="Creates a named snapshot",
        reason="nl request",
        expires_at="2026-07-21T12:05:00Z",
        crisis=False,
        requires_deepseek=False,
    )
    pipe_result = PipelineResult(
        reply_text="Propuesta lista.",
        escalate_notify_text=None,
        pending_hitl=[pending],
        crisis=False,
        deepseek_consulted=False,
        route="worker",
    )

    with patch("handlers.run_pipeline", return_value=pipe_result):
        await nl_message_handler(update, context)

    # One of the replies must include keyboard (approval card)
    keyboard_calls = [
        c
        for c in update.message.reply_text.await_args_list
        if c.kwargs.get("reply_markup") is not None
    ]
    assert len(keyboard_calls) >= 1
    markup = keyboard_calls[0].kwargs["reply_markup"]
    row = markup.inline_keyboard[0]
    assert any("approve:abc123def456" == b.callback_data for b in row)
    assert any("reject:abc123def456" == b.callback_data for b in row)


@pytest.mark.asyncio
async def test_nl_crisis_pending_badge_on_card(authorized_settings):
    gate = MagicMock()
    store = MagicMock()
    context = _make_context(gate=gate, store=store)
    update = _message_update(user_id=111, text="crisis reinicia vm 300")

    pending = ProposeResult(
        status="pending",
        needs_approval=True,
        message="🔐 Aprobación requerida\n\nAcción: vm_reboot\n",
        request_id="deadbeefcafebabe",
        hitl_request_id="deadbeefcafebabe",
        action_id="vm_reboot",
        tier="write",
        target="VM 300",
        expected_impact="Reboots VM",
        reason="crisis path",
        expires_at="2026-07-21T12:05:00Z",
        crisis=False,
        requires_deepseek=False,
    )
    pipe_result = PipelineResult(
        reply_text="DeepSeek recomienda reboot controlado.",
        escalate_notify_text="⚠️ Escalando a Crisis (DeepSeek): motivo=keyword:crisis",
        pending_hitl=[pending],
        crisis=True,
        deepseek_consulted=True,
        route="crisis",
    )

    with patch("handlers.run_pipeline", return_value=pipe_result):
        await nl_message_handler(update, context)

    keyboard_calls = [
        c
        for c in update.message.reply_text.await_args_list
        if c.kwargs.get("reply_markup") is not None
    ]
    assert keyboard_calls
    card = keyboard_calls[0].args[0]
    assert "DeepSeek" in card
    assert "stub Phase 3" not in card
    assert "sin invocación real" not in card


@pytest.mark.asyncio
async def test_cmd_list_vms_does_not_call_run_pipeline(authorized_settings):
    gate = MagicMock()
    gate.propose.return_value = ProposeResult(
        status="completed",
        needs_approval=False,
        message="ok",
        execution_result="VM 100 running",
        result="VM 100 running",
    )
    context = _make_context(gate=gate)
    update = _message_update(user_id=111, text="/list_vms")

    with patch("handlers.run_pipeline") as mock_pipe:
        await cmd_list_vms(update, context)
        mock_pipe.assert_not_called()

    gate.propose.assert_called_once()
    assert gate.propose.call_args.args[0] == "list_vms"


@pytest.mark.asyncio
async def test_nl_empty_whitespace_ignored(authorized_settings):
    gate = MagicMock()
    context = _make_context(gate=gate)
    update = _message_update(user_id=111, text="   ")

    with patch("handlers.run_pipeline") as mock_pipe:
        await nl_message_handler(update, context)
        mock_pipe.assert_not_called()
