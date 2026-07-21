"""Telegram bot — Punto de entrada principal."""
import logging
from pathlib import Path

from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler

from config import settings
from handlers import cmd_start, cmd_help, cmd_health, cmd_vm, approval_callback
from scheduler import ReportScheduler
from health_reporter import HealthReporter
from vm_diagnostics import VMDiagnostics
from chat_store import ChatStore

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main():
    if not settings.telegram_bot_token:
        logger.error("TELEGRAM_BOT_TOKEN no configurado")
        return

    app = ApplicationBuilder().token(settings.telegram_bot_token).build()

    # Registrar comandos
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("vm", cmd_vm))

    # Phase 3 scaffold: callback para inline keyboards (approve/deny) — D-09
    app.add_handler(
        CallbackQueryHandler(approval_callback, pattern="^(approve|reject):")
    )

    # Persistencia de chats autorizados (daily report recipients in 02-02)
    data_dir = Path("/app/data")
    data_dir.mkdir(parents=True, exist_ok=True)
    app.bot_data["chat_store"] = ChatStore(str(data_dir / "known_chats.sqlite"))

    # Adjuntar dependencias al contexto del bot
    app.bot_data["health_reporter"] = HealthReporter()
    app.bot_data["vm_diagnostics"] = VMDiagnostics()

    # Programar reporte diario (JobQueue wiring completa en 02-02)
    scheduler = ReportScheduler()
    scheduler.setup(app)

    logger.info("Bot iniciado — long polling activo")
    # D-02: long polling only; no webhook / no published ports
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
