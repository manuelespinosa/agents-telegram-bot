"""Programador de tareas periódicas (stub — implementación completa en 02-02).

En Phase 2 se completará con APScheduler para:
- Reporte diario de salud a las 08:00
"""
import logging
from telegram.ext import Application

logger = logging.getLogger(__name__)


class ReportScheduler:
    """Gestiona tareas programadas del bot.

    En 02-02 se implementará con APScheduler para enviar
    reportes diarios de salud a las 08:00.
    """

    def setup(self, app: Application):
        """Configurar tareas programadas.

        Args:
            app: Instancia de Application del bot de Telegram.
            Las implementaciones en 02-02 añadirán aquí los jobs de APScheduler.
        """
        logger.info(
            "ReportScheduler: Programación de tareas disponible en Phase 2. "
            "Por ahora, solicita /health manualmente."
        )
