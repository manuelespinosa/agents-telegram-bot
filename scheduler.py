"""Programador de tareas periódicas (stub — implementación completa en 02-02).

En Phase 2 se completará con APScheduler para:
- Reporte diario de salud a las 08:00
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ReportScheduler:
    """Gestiona tareas programadas del bot.

    En 02-02 se implementará con JobQueue (python-telegram-bot[job-queue])
    para enviar reportes diarios de salud a las 08:00.
    """

    def setup(self, app: Any):
        """Configurar tareas programadas.

        Args:
            app: Instancia de Application del bot de Telegram.
            Las implementaciones en 02-02 registrarán run_daily aquí.
        """
        logger.info(
            "ReportScheduler: Programación de tareas disponible en Phase 2. "
            "Por ahora, solicita /health manualmente."
        )
