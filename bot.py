"""Telegram bot — Punto de entrada principal (Phase 2–4: monitor + HITL + NL pipeline)."""
import logging
from pathlib import Path

from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from action_executor import ActionExecutor
from action_gate import ActionGate
from budget_gate import BudgetGate
from chat_store import ChatStore
from config import settings
from handlers import (
    approval_callback,
    cmd_health,
    cmd_help,
    cmd_list_vms,
    cmd_resume_budget,
    cmd_start,
    cmd_vm,
    cmd_vm_reboot,
    cmd_vm_start,
    cmd_vm_stop,
    nl_message_handler,
)
from health_reporter import HealthReporter
from hitl_store import HITLStore
from pipeline.clarification_store import ClarificationStore
from scheduler import ReportScheduler
from vm_diagnostics import VMDiagnostics

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

MIN_HMAC_SECRET_BYTES = 32


def _resolve_hmac_secret() -> tuple[bytes, bool]:
    """Return (secret_bytes, writes_enabled). Fail closed if short/missing."""
    raw = (settings.hitl_hmac_secret or "").encode("utf-8")
    if len(raw) < MIN_HMAC_SECRET_BYTES:
        logger.error(
            "HITL_HMAC_SECRET missing or shorter than %s bytes — "
            "mutation proposes disabled (fail closed). Reads remain active.",
            MIN_HMAC_SECRET_BYTES,
        )
        # Placeholder so ActionGate can construct; handlers refuse writes.
        return b"\x00" * MIN_HMAC_SECRET_BYTES, False
    return raw, True


def main():
    if not settings.telegram_bot_token:
        logger.error("TELEGRAM_BOT_TOKEN no configurado")
        return

    app = ApplicationBuilder().token(settings.telegram_bot_token).build()

    # Data volume for SQLite (chat store, HITL, budget state)
    data_dir = Path("/app/data")
    data_dir.mkdir(parents=True, exist_ok=True)

    # Persistencia de chats autorizados (daily report recipients)
    app.bot_data["chat_store"] = ChatStore(str(data_dir / "known_chats.sqlite"))

    # Phase 2 read-only deps
    app.bot_data["health_reporter"] = HealthReporter()
    app.bot_data["vm_diagnostics"] = VMDiagnostics()

    # Phase 3 safety core wiring
    hmac_secret, writes_enabled = _resolve_hmac_secret()
    hitl_path = settings.hitl_db_path or str(data_dir / "hitl.sqlite")
    cost_path = settings.cost_db_path or str(data_dir / "cost_logs.db")

    hitl_store = HITLStore(hitl_path)
    budget_gate = BudgetGate(
        cost_db_path=cost_path,
        state_db_path=hitl_path,
        max_usd=float(settings.budget_max_usd),
    )
    executor = ActionExecutor()
    action_gate = ActionGate(
        store=hitl_store,
        budget=budget_gate,
        hmac_secret=hmac_secret,
        executor=executor,
        timeout_sec=int(settings.hitl_timeout_sec or 300),
    )

    app.bot_data["hitl_store"] = hitl_store
    app.bot_data["budget_gate"] = budget_gate
    app.bot_data["action_executor"] = executor
    app.bot_data["action_gate"] = action_gate
    app.bot_data["hitl_writes_enabled"] = writes_enabled
    app.bot_data["cost_db_path"] = cost_path

    # Phase 4 multi-agent NL pipeline (D-01) — ClarificationStore co-located with HITL data
    pipeline_enabled = True
    try:
        import crewai  # noqa: F401
    except ImportError:
        logger.error(
            "crewai package missing after rebuild — NL pipeline may fail at runtime. "
            "Rebuild telegram-bot image with requirements.txt (crewai pin)."
        )
    clarification_path = str(Path(hitl_path).parent / "pipeline.sqlite")
    try:
        clarification_store = ClarificationStore(clarification_path)
    except Exception:
        logger.exception("ClarificationStore init failed — using hitl db path fallback")
        clarification_store = ClarificationStore(hitl_path)
    app.bot_data["clarification_store"] = clarification_store
    app.bot_data["pipeline_enabled"] = pipeline_enabled

    # Commands — ActionGate direct, zero LLM (D-02). Read paths ignore budget pause (D-14).
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("vm", cmd_vm))
    app.add_handler(CommandHandler("list_vms", cmd_list_vms))
    app.add_handler(CommandHandler("vm_start", cmd_vm_start))
    app.add_handler(CommandHandler("vm_stop", cmd_vm_stop))
    app.add_handler(CommandHandler("vm_reboot", cmd_vm_reboot))
    app.add_handler(CommandHandler("resume-budget", cmd_resume_budget))
    app.add_handler(CommandHandler("resume_budget", cmd_resume_budget))

    # NL free-text → pipeline (group=1 after CommandHandlers so slash never hits NL)
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, nl_message_handler),
        group=1,
    )

    # HITL callbacks — UUID only (D-05)
    app.add_handler(
        CallbackQueryHandler(approval_callback, pattern="^(approve|reject):")
    )

    # Programar reporte diario (JobQueue; also used for HITL expire)
    scheduler = ReportScheduler()
    scheduler.setup(app)

    logger.info(
        "Bot iniciado — long polling activo (HITL writes=%s, pipeline=%s)",
        "enabled" if writes_enabled else "DISABLED",
        "enabled" if pipeline_enabled else "DISABLED",
    )
    # D-02: long polling only; no webhook / no published ports
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
