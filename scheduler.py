"""Programador de reporte diario vía PTB JobQueue (MON-03 / D-04).

Uses application.job_queue.run_daily only — never a second independent
scheduler loop beside run_polling (lifecycle conflicts).
"""
from __future__ import annotations

import logging
from datetime import time
from typing import Any
from zoneinfo import ZoneInfo

from config import settings

logger = logging.getLogger(__name__)


async def daily_job(context: Any) -> None:
    """JobQueue callback: send daily health report."""
    reporter = context.application.bot_data.get("health_reporter")
    if reporter is None:
        logger.error("daily_job: health_reporter missing from bot_data")
        return
    try:
        await reporter.send_daily_report(context.application)
    except Exception as e:
        logger.error("daily_job failed: %s", e)


class ReportScheduler:
    """Registra el job diario daily_health_report en JobQueue."""

    def setup(self, app: Any) -> None:
        """Configurar run_daily a report_hour/minute en report_tz.

        Raises:
            RuntimeError: if job_queue is None (missing [job-queue] extra).
        """
        if app.job_queue is None:
            raise RuntimeError(
                "JobQueue no disponible. Instala "
                "python-telegram-bot[job-queue]."
            )

        tz = ZoneInfo(settings.report_tz)
        when = time(
            hour=int(settings.report_hour),
            minute=int(settings.report_minute),
            tzinfo=tz,
        )
        app.job_queue.run_daily(
            daily_job,
            time=when,
            name="daily_health_report",
        )
        logger.info(
            "ReportScheduler: job daily_health_report registered at "
            "%02d:%02d %s",
            settings.report_hour,
            settings.report_minute,
            settings.report_tz,
        )
