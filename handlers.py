"""Manejadores de comandos del bot de Telegram (Phase 2–4: monitor + HITL + NL)."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from action_catalog import ActionId
from action_gate import ProposeResult
from config import is_user_authorized, settings
from pipeline.orchestrator import run_pipeline
from report_format import split_message

logger = logging.getLogger(__name__)

# approve:<32 hex> or reject:<32 hex> — UUID hex only in callback_data
_CALLBACK_RE = re.compile(r"^(approve|reject):([0-9a-fA-F]{8,64})$")

# Conservative single timeout for router+worker (~60s) and crisis (~120s)
PIPELINE_TIMEOUT_SEC = 120


async def require_authorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Deny-if-empty allowlist guard. Records authorized chats in ChatStore.

    Returns True when the user may proceed; False after sending a denial.
    """
    allowed = settings.allowed_user_ids()
    uid = update.effective_user.id if update.effective_user else None

    if not is_user_authorized(uid, allowed):
        logger.warning("Unauthorized telegram user id=%s", uid)
        if update.message:
            await update.message.reply_text("⛔ No autorizado.")
        elif update.callback_query:
            await update.callback_query.answer("⛔ No autorizado.", show_alert=True)
        return False

    store = context.application.bot_data.get("chat_store")
    chat = update.effective_chat
    if store is not None and chat is not None:
        try:
            store.add_chat(chat.id)
        except Exception as e:
            logger.error("ChatStore.add_chat failed: %s", e)
    return True


async def _reply_chunks(update: Update, text: str) -> None:
    """Reply with plain text chunks (no Markdown on dynamic content)."""
    for chunk in split_message(text or ""):
        await update.message.reply_text(chunk)


def format_approval_message(
    action_id: str,
    target: str,
    tier: str,
    impact: str,
    reason: str,
    expires_at: str,
    crisis: bool = False,
    deepseek_consulted: bool = False,
    *,
    crisis_stub: bool = False,
) -> str:
    """Full plain-text approval context (D-06 / D-11). No secrets.

    ``crisis_stub`` is accepted for backward-compat call sites and treated as
    crisis=True (Phase 3 stub copy is no longer shown).
    """
    crisis_flag = bool(crisis or crisis_stub)
    deepseek_flag = bool(deepseek_consulted or crisis_stub)
    if crisis_flag or deepseek_flag:
        crisis_line = (
            "\n🚨 CRISIS — DeepSeek consultado. Una sola aprobación (Approve/Deny).\n"
        )
    else:
        crisis_line = "\n"
    return (
        f"🔐 Aprobación requerida\n\n"
        f"Acción: {action_id}\n"
        f"Target: {target}\n"
        f"Tier: {tier}\n"
        f"Impacto: {impact}\n"
        f"Motivo: {reason}\n"
        f"Caduca: {expires_at} (5 min)\n"
        f"{crisis_line}"
        f"Timeout → se cancela (nunca auto-ejecuta)."
    )


def approval_keyboard(request_id: str) -> InlineKeyboardMarkup:
    """Approve / Deny only (D-05). callback_data under 64 bytes."""
    rid = str(request_id)
    approve_data = f"approve:{rid}"
    reject_data = f"reject:{rid}"
    if len(approve_data.encode("utf-8")) > 64 or len(reject_data.encode("utf-8")) > 64:
        raise ValueError("callback_data exceeds Telegram 64-byte limit")
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Aprobar", callback_data=approve_data),
                InlineKeyboardButton("❌ Denegar", callback_data=reject_data),
            ]
        ]
    )


def _writes_enabled(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool(context.application.bot_data.get("hitl_writes_enabled", False))


def _actor(update: Update) -> str:
    uid = update.effective_user.id if update.effective_user else "unknown"
    return f"telegram:{uid}"


async def _schedule_hitl_expire(
    context: ContextTypes.DEFAULT_TYPE, request_id: str
) -> None:
    """Schedule D-07 expire job (never auto-executes)."""
    job_queue = context.application.job_queue
    if job_queue is None:
        logger.warning("JobQueue unavailable — HITL expire not scheduled for %s", request_id)
        return
    timeout = int(getattr(settings, "hitl_timeout_sec", 300) or 300)
    name = f"hitl-expire-{request_id}"
    # Drop any prior job with same name (re-propose same id is rare)
    for job in job_queue.get_jobs_by_name(name):
        job.schedule_removal()
    job_queue.run_once(
        hitl_expire_job,
        when=timeout,
        data={"request_id": request_id},
        name=name,
    )


async def hitl_expire_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue callback: expire pending only; edit message if known (D-07)."""
    data = context.job.data if context.job else None
    if isinstance(data, dict):
        request_id = data.get("request_id")
    else:
        request_id = data
    if not request_id:
        return

    gate = context.application.bot_data.get("action_gate")
    store = context.application.bot_data.get("hitl_store")
    if gate is None:
        logger.error("hitl_expire_job: action_gate missing")
        return

    expired = gate.expire(str(request_id))
    if not expired:
        return

    chat_id = None
    message_id = None
    if store is not None:
        req = store.get(str(request_id))
        if req is not None:
            chat_id = req.chat_id
            message_id = req.message_id

    if chat_id is None or message_id is None:
        return

    text = (
        f"⏰ Solicitud expirada\n\n"
        f"request_id: {request_id}\n"
        f"No se ejecutó ninguna acción (timeout 5 min)."
    )
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
        )
    except Exception as e:
        logger.warning("expire edit_message_text failed: %s", e)


async def _send_pending_approval(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    result: ProposeResult,
    *,
    crisis: bool | None = None,
    deepseek_consulted: bool | None = None,
) -> None:
    """Send approval message + keyboard; bind message ids; schedule expire."""
    request_id = result.request_id or result.hitl_request_id
    if not request_id:
        await update.message.reply_text("❌ Error interno: HITL sin request_id.")
        return

    crisis_flag = (
        bool(crisis)
        if crisis is not None
        else bool(result.crisis or result.requires_deepseek)
    )
    deepseek_flag = (
        bool(deepseek_consulted)
        if deepseek_consulted is not None
        else bool(
            getattr(result, "deepseek_consulted", False) or result.requires_deepseek
        )
    )

    text = format_approval_message(
        action_id=result.action_id or "?",
        target=result.target or "?",
        tier=result.tier or "write",
        impact=result.expected_impact or "?",
        reason=result.reason or "",
        expires_at=result.expires_at or "?",
        crisis=crisis_flag,
        deepseek_consulted=deepseek_flag,
    )
    # Prefer gate message when fully formatted AND no crisis badge override needed
    if result.message and "Aprobación requerida" in result.message:
        needs_badge = crisis_flag or deepseek_flag
        has_badge = "CRISIS" in result.message or "DeepSeek consultado" in result.message
        if not needs_badge or has_badge:
            # Drop legacy Phase 3 stub wording if present on gate copy
            if "stub Phase 3" in result.message or "sin invocación real" in result.message:
                text = format_approval_message(
                    action_id=result.action_id or "?",
                    target=result.target or "?",
                    tier=result.tier or "write",
                    impact=result.expected_impact or "?",
                    reason=result.reason or "",
                    expires_at=result.expires_at or "?",
                    crisis=True,
                    deepseek_consulted=True,
                )
            else:
                text = result.message

    keyboard = approval_keyboard(request_id)
    sent = await update.message.reply_text(text, reply_markup=keyboard)

    store = context.application.bot_data.get("hitl_store")
    if store is not None and sent is not None:
        try:
            store.bind_telegram_message(request_id, sent.chat_id, sent.message_id)
        except Exception as e:
            logger.error("bind_telegram_message failed: %s", e)

    await _schedule_hitl_expire(context, request_id)


async def nl_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Authorized free-text → multi-agent pipeline (D-01). Slash path stays LLM-free (D-02)."""
    if not await require_authorized(update, context):
        return

    if update.message is None:
        return

    raw = update.message.text or ""
    text = raw.strip()
    if not text:
        return

    if not context.application.bot_data.get("pipeline_enabled", False):
        logger.warning("NL message ignored — pipeline_enabled is False")
        return

    gate = context.application.bot_data.get("action_gate")
    if gate is None:
        await update.message.reply_text("❌ ActionGate no configurado.")
        return

    clarification_store = context.application.bot_data.get("clarification_store")
    budget = context.application.bot_data.get("budget_gate")
    cost_path = context.application.bot_data.get("cost_db_path") or settings.cost_db_path
    uid = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0
    actor = _actor(update)

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                run_pipeline,
                text,
                actor=actor,
                chat_id=chat_id,
                gate=gate,
                clarification_store=clarification_store,
                cost_db_path=cost_path,
                budget=budget,
                user_id=uid,
            ),
            timeout=PIPELINE_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        logger.error(
            "pipeline timeout after %ss chat_id=%s", PIPELINE_TIMEOUT_SEC, chat_id
        )
        await update.message.reply_text(
            "⏱️ Pipeline timeout. Intenta de nuevo o usa un comando slash."
        )
        return
    except Exception:
        logger.exception("pipeline failed chat_id=%s", chat_id)
        await update.message.reply_text(
            "❌ Error en el pipeline multi-agente. "
            "Intenta de nuevo o usa un comando slash."
        )
        return

    # D-12: short escalate notify before analysis / HITL cards
    if result.escalate_notify_text:
        await update.message.reply_text(result.escalate_notify_text)

    if result.pending_hitl:
        for pending in result.pending_hitl:
            propose = _coerce_propose_result(pending)
            if propose is None:
                logger.error("pending_hitl item not a ProposeResult: %r", type(pending))
                continue
            await _send_pending_approval(
                update,
                context,
                propose,
                crisis=bool(result.crisis or propose.crisis or propose.requires_deepseek),
                deepseek_consulted=bool(
                    result.deepseek_consulted or propose.requires_deepseek
                ),
            )

    if result.reply_text:
        await _reply_chunks(update, result.reply_text)


def _coerce_propose_result(pending: Any) -> ProposeResult | None:
    """Accept ProposeResult or a duck-typed pending HITL object."""
    if isinstance(pending, ProposeResult):
        return pending
    if pending is None:
        return None
    # Minimal reconstruction from gate-like objects
    request_id = getattr(pending, "request_id", None) or getattr(
        pending, "hitl_request_id", None
    )
    if not request_id and isinstance(pending, dict):
        request_id = pending.get("request_id") or pending.get("hitl_request_id")
        return ProposeResult(
            status=str(pending.get("status") or "pending"),
            needs_approval=bool(pending.get("needs_approval", True)),
            message=str(pending.get("message") or ""),
            request_id=request_id,
            hitl_request_id=request_id,
            action_id=pending.get("action_id"),
            tier=pending.get("tier"),
            target=pending.get("target"),
            expected_impact=pending.get("expected_impact"),
            reason=pending.get("reason"),
            expires_at=pending.get("expires_at"),
            requires_deepseek=bool(pending.get("requires_deepseek", False)),
            crisis=bool(pending.get("crisis", False)),
        )
    if request_id:
        return ProposeResult(
            status=str(getattr(pending, "status", "pending") or "pending"),
            needs_approval=bool(getattr(pending, "needs_approval", True)),
            message=str(getattr(pending, "message", "") or ""),
            request_id=str(request_id),
            hitl_request_id=str(request_id),
            action_id=getattr(pending, "action_id", None),
            tier=getattr(pending, "tier", None),
            target=getattr(pending, "target", None),
            expected_impact=getattr(pending, "expected_impact", None),
            reason=getattr(pending, "reason", None),
            expires_at=getattr(pending, "expires_at", None),
            requires_deepseek=bool(getattr(pending, "requires_deepseek", False)),
            crisis=bool(getattr(pending, "crisis", False)),
        )
    return None


async def _propose_write(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    action_id: str,
    vmid: int,
    reason: str,
) -> None:
    if not _writes_enabled(context):
        await update.message.reply_text(
            "🛑 Mutaciones deshabilitadas: HITL_HMAC_SECRET ausente o < 32 bytes.\n"
            "Configura el secreto en /home/ai-agents/.env y reinicia el bot."
        )
        return

    gate = context.application.bot_data.get("action_gate")
    if gate is None:
        await update.message.reply_text("❌ ActionGate no configurado.")
        return

    result = gate.propose(
        action_id,
        {"vmid": vmid},
        reason=reason,
        actor=_actor(update),
    )

    if result.status == "blocked":
        # D-16 budget alert text already in result.message
        await update.message.reply_text(result.message or "⛔ Operación bloqueada.")
        return

    if result.needs_approval and result.status == "pending":
        await _send_pending_approval(update, context, result)
        return

    await update.message.reply_text(result.message or f"Estado: {result.status}")


# ── Comandos básicos ────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bienvenida e instrucciones básicas."""
    if not await require_authorized(update, context):
        return
    await update.message.reply_text(
        "🤖 AI SysAdmin Homelab\n\n"
        "Monitor read-only + HITL para mutaciones Proxmox.\n\n"
        "Comandos:\n"
        "• /health — Reporte de salud del homelab\n"
        "• /list_vms — Inventario VMs/CTs (read)\n"
        "• /vm <id> — Diagnóstico de VM\n"
        "• /vm_start|/vm_stop|/vm_reboot <id> — proponer mutación (HITL)\n"
        "• /resume-budget — reanudar mutaciones tras kill-switch\n"
        "• /help — Ayuda detallada\n\n"
        "🔒 Escritura solo tras Aprobar (5 min timeout, nunca auto-ejecuta)."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ayuda detallada."""
    if not await require_authorized(update, context):
        return
    await update.message.reply_text(
        "📋 Comandos disponibles\n\n"
        "Lectura (sin aprobación):\n"
        "/health — Estado nodos, VMs, storage y eventos Docker\n"
        "/list_vms — Inventario corto del clúster\n"
        "/vm <id> — Diagnóstico detallado (CPU, RAM, snapshots)\n\n"
        "Mutaciones (HITL — Aprobar/Denegar, caduca 5 min):\n"
        "/vm_start <id>\n"
        "/vm_stop <id>\n"
        "/vm_reboot <id>\n\n"
        "Budget:\n"
        "/resume-budget — limpia kill-switch de mutaciones (D-15)\n"
        "  (lecturas siguen activas aunque mutaciones estén pausadas)\n\n"
        "/start — Bienvenida\n"
        "/help — Esta ayuda\n\n"
        "⚠️ Nunca pruebes mutaciones en OPNsense/PBS/infra crítica.\n"
        "E2E seguro: VMID 300 (no crítico)."
    )


# ── /health — Reporte de salud ──────────────────────────────

async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reporte de salud del homelab (no consulta budget pause — D-14)."""
    if not await require_authorized(update, context):
        return
    try:
        reporter = context.application.bot_data.get("health_reporter")
        if reporter and hasattr(reporter, "collect_all_health"):
            report = await reporter.collect_all_health()
            await _reply_chunks(
                update,
                report
                or "📊 No se pudo generar el reporte de salud.",
            )
        else:
            await update.message.reply_text(
                "📊 HealthReporter no está configurado en este proceso."
            )
    except Exception as e:
        logger.error("Error en cmd_health: %s", e)
        await update.message.reply_text("❌ Error al obtener reporte de salud.")


# ── /vm <id> — Diagnóstico de VM ────────────────────────────

async def cmd_vm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Diagnóstico de máquina virtual o contenedor (read path)."""
    if not await require_authorized(update, context):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "⚠️ Uso: /vm <ID>\n\n"
            "Ejemplo: /vm 100\n\n"
            "El ID es el número de la VM o contenedor en Proxmox."
        )
        return

    vmid = int(context.args[0])
    try:
        diagnostics = context.application.bot_data.get("vm_diagnostics")
        if diagnostics and hasattr(diagnostics, "diagnose"):
            result = await diagnostics.diagnose(vmid)
            await _reply_chunks(
                update,
                result or f"❌ Sin datos para VM {vmid}.",
            )
        else:
            await update.message.reply_text(
                "🔍 VMDiagnostics no está configurado en este proceso."
            )
    except Exception as e:
        logger.error("Error en cmd_vm: %s", e)
        await update.message.reply_text(
            f"❌ Error al obtener diagnóstico de VM {vmid}. "
            "Verifica que el ID existe."
        )


# ── Read propose: /list_vms ─────────────────────────────────

async def cmd_list_vms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List cluster VMs via ActionGate read path (no HITL)."""
    if not await require_authorized(update, context):
        return
    gate = context.application.bot_data.get("action_gate")
    if gate is None:
        await update.message.reply_text("❌ ActionGate no configurado.")
        return
    try:
        result = gate.propose(
            ActionId.LIST_VMS.value,
            {},
            reason="operator /list_vms",
            actor=_actor(update),
        )
        body = result.execution_result or result.result or result.message
        await _reply_chunks(update, body or "Sin datos.")
    except Exception as e:
        logger.error("cmd_list_vms failed: %s", e)
        await update.message.reply_text("❌ Error al listar VMs.")


# ── Write propose commands ──────────────────────────────────

def _parse_vmid_arg(context: ContextTypes.DEFAULT_TYPE) -> int | None:
    if not context.args or not context.args[0].isdigit():
        return None
    return int(context.args[0])


async def cmd_vm_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_authorized(update, context):
        return
    vmid = _parse_vmid_arg(context)
    if vmid is None:
        await update.message.reply_text("⚠️ Uso: /vm_start <VMID>\nEjemplo: /vm_start 300")
        return
    await _propose_write(
        update, context, ActionId.VM_START.value, vmid, reason=f"operator /vm_start {vmid}"
    )


async def cmd_vm_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_authorized(update, context):
        return
    vmid = _parse_vmid_arg(context)
    if vmid is None:
        await update.message.reply_text("⚠️ Uso: /vm_stop <VMID>\nEjemplo: /vm_stop 300")
        return
    await _propose_write(
        update, context, ActionId.VM_STOP.value, vmid, reason=f"operator /vm_stop {vmid}"
    )


async def cmd_vm_reboot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_authorized(update, context):
        return
    vmid = _parse_vmid_arg(context)
    if vmid is None:
        await update.message.reply_text(
            "⚠️ Uso: /vm_reboot <VMID>\nEjemplo: /vm_reboot 300"
        )
        return
    await _propose_write(
        update,
        context,
        ActionId.VM_REBOOT.value,
        vmid,
        reason=f"operator /vm_reboot {vmid}",
    )


async def cmd_resume_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manual budget kill-switch recovery (D-15)."""
    if not await require_authorized(update, context):
        return
    budget = context.application.bot_data.get("budget_gate")
    if budget is None:
        await update.message.reply_text("❌ BudgetGate no configurado.")
        return
    try:
        budget.clear_paused()
        cost = budget.rolling_cost_24h() if hasattr(budget, "rolling_cost_24h") else None
        allowed = budget.mutations_allowed() if hasattr(budget, "mutations_allowed") else True
        extra = ""
        if cost is not None:
            max_usd = getattr(budget, "max_usd", settings.budget_max_usd)
            extra = f"\nCoste 24h: ${cost:.4f} / ${max_usd:.2f}"
        if not allowed:
            extra += (
                "\n⚠️ Aún por encima del tope: mutaciones pueden seguir bloqueadas "
                "hasta que baje el coste 24h."
            )
        await update.message.reply_text(
            f"✅ Kill-switch limpiado (clear_paused).{extra}\n"
            f"Mutaciones: {'permitidas' if allowed else 'aún restringidas por coste'}."
        )
    except Exception as e:
        logger.error("cmd_resume_budget failed: %s", e)
        await update.message.reply_text("❌ Error al reanudar budget.")


# ── HITL approval callbacks (HITL-02 / D-05 / D-08) ──────────

async def approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Approve/Deny via UUID-only callback_data; execute through ActionGate."""
    if not await require_authorized(update, context):
        return

    query = update.callback_query
    if query is None or not query.data:
        return

    await query.answer()

    m = _CALLBACK_RE.match(query.data)
    if not m:
        await query.edit_message_text("❌ callback_data inválido.")
        return

    action, request_id = m.group(1), m.group(2)
    gate = context.application.bot_data.get("action_gate")
    if gate is None:
        await query.edit_message_text("❌ ActionGate no configurado.")
        return

    uid = update.effective_user.id if update.effective_user else 0

    if action == "approve":
        if not _writes_enabled(context):
            await query.edit_message_text(
                "🛑 Aprobación bloqueada: HITL_HMAC_SECRET no configurado "
                "(fail closed)."
            )
            return
        decision = gate.approve(request_id, uid)
        # Cancel expire job if present
        _cancel_expire_job(context, request_id)
        body = (
            f"✅ {decision.message}\n\n"
            f"request_id: {request_id}\n"
            f"status: {decision.status}"
        )
        if decision.execution_result:
            body += f"\n\n{decision.execution_result}"
        await query.edit_message_text(body)
        return

    if action == "reject":
        decision = gate.reject(request_id, uid)
        _cancel_expire_job(context, request_id)
        await query.edit_message_text(
            f"❌ {decision.message}\n\n"
            f"request_id: {request_id}\n"
            f"status: {decision.status}\n"
            f"Sin ejecución en Proxmox."
        )
        return

    await query.edit_message_text(f"Acción desconocida: {action}")


def _cancel_expire_job(context: ContextTypes.DEFAULT_TYPE, request_id: str) -> None:
    job_queue = context.application.job_queue
    if job_queue is None:
        return
    name = f"hitl-expire-{request_id}"
    for job in job_queue.get_jobs_by_name(name):
        job.schedule_removal()


# Re-export catalog symbols used only for typing clarity in tests
__all__ = [
    "require_authorized",
    "cmd_start",
    "cmd_help",
    "cmd_health",
    "cmd_vm",
    "cmd_list_vms",
    "cmd_vm_start",
    "cmd_vm_stop",
    "cmd_vm_reboot",
    "cmd_resume_budget",
    "nl_message_handler",
    "approval_callback",
    "approval_keyboard",
    "format_approval_message",
    "hitl_expire_job",
]
