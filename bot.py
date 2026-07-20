"""Telegram bot — Punto de entrada principal."""
import logging
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler
from config import settings
from handlers import cmd_start, cmd_help, cmd_health, cmd_vm, approval_callback
from scheduler import ReportScheduler
from health_reporter import HealthReporter
from vm_diagnostics import VMDiagnostics

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main():
    if not settings.telegram_token:
        logger.error("TELEGRAM_BOT_TOKEN no configurado")
        return

    app = ApplicationBuilder().token(settings.telegram_token).build()

    # Registrar comandos
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("vm", cmd_vm))

    # Phase 3 scaffold: callback para inline keyboards (approve/deny)
    app.add_handler(CallbackQueryHandler(approval_callback, pattern="^(approve|reject):"))

    # Adjuntar dependencias al contexto del bot
    app.bot_data["health_reporter"] = HealthReporter()
    app.bot_data["vm_diagnostics"] = VMDiagnostics()

    # Programar reporte diario
    scheduler = ReportScheduler()
    scheduler.setup(app)

    logger.info("Bot iniciado — long polling activo")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
