"""Crawler de logs de Docker (stub — implementación completa en 02-02).

En Phase 2 se implementará:
- Leer logs recientes de contenedores vía Docker socket
- Extraer eventos críticos (ERROR, FATAL, OOM, disk full)
- Resumir eventos para pasar a LLM (no raw data)
- Control de tokens: sliding window, max 12K tokens por servicio
"""
import logging

logger = logging.getLogger(__name__)


class LogCrawler:
    """Lee logs de Docker y extrae eventos relevantes."""

    async def get_critical_events(self, container: str = "", lines: int = 100) -> list:
        """Obtener eventos críticos de logs de Docker.

        Args:
            container: Nombre del contenedor (vacío = todos).
            lines: Número de líneas recientes a leer.

        Returns:
            list[dict]: Lista de eventos críticos con timestamp, nivel y mensaje.
        """
        logger.info("LogCrawler: Crawler de logs disponible en Phase 2.")
        return []

    async def summarize(self, events: list) -> str:
        """Resumir eventos críticos para el LLM (evita pasar raw data).

        Args:
            events: Lista de eventos críticos de get_critical_events().

        Returns:
            str: Resumen estructurado para incluir en el reporte.
        """
        logger.info("LogCrawler: Summarizer disponible en Phase 2.")
        return "• *Logs:* No hay eventos críticos recientes.\n"
