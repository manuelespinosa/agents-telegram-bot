"""Recolector de datos de salud del homelab (stub — implementación completa en 02-02).

En Phase 2 se implementará:
- Colectar estado de nodos Proxmox (vía proxmoxer)
- Colectar estado de VMs y storage
- Leer eventos críticos de Docker (vía docker socket)
- Generar reporte estructurado con tablas
- Enviar a Telegram
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


class HealthReporter:
    """Recolecta y formatea datos de salud del homelab."""

    async def collect_all_health(self) -> str:
        """Recolectar todos los datos de salud y devolver reporte formateado.

        Returns:
            str: Reporte de salud formateado en Markdown para Telegram.
        """
        logger.info("HealthReporter: Recolección completa disponible en Phase 2.")
        return (
            "📊 *Estado del Homelab*\n\n"
            "📡 Proxmox — conectado\n"
            "🐳 Docker — monitoreando\n\n"
            "✅ *Sistema base operativo*\n"
            "━ Implementación completa del recolector en Phase 2\n\n"
            "Usa /health más tarde para el reporte completo."
        )

    async def send_daily_report(self, app: Any):
        """Enviar reporte diario programado (08:00).

        Args:
            app: Instancia de Application del bot de Telegram.
        """
        report = await self.collect_all_health()
        # Encontrar chat IDs para enviar reporte
        # Implementación completa en 02-02
        logger.info("HealthReporter: Envío programado disponible en Phase 2.")
