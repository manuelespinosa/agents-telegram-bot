"""Diagnóstico de VMs (stub — implementación completa en 02-02).

En Phase 2 se implementará:
- Consultar estado de VM vía Proxmox API (proxmoxer)
- CPU%, RAM%, uptime, estado del agente QEMU
- Listar snapshots disponibles
- Devolver diagnóstico formateado
"""
import logging

logger = logging.getLogger(__name__)


class VMDiagnostics:
    """Diagnóstico read-only de máquinas virtuales."""

    async def diagnose(self, vmid: int) -> str:
        """Obtener diagnóstico completo de una VM.

        Args:
            vmid: ID de la máquina virtual o contenedor.

        Returns:
            str: Diagnóstico formateado en Markdown para Telegram.
        """
        logger.info(f"VMDiagnostics: Diagnóstico de VM {vmid} disponible en Phase 2.")
        return (
            f"🔍 *Diagnóstico VM {vmid}*\n\n"
            "Consultando Proxmox API...\n\n"
            "*Nota:* El diagnóstico completo estará disponible "
            "en la siguiente fase de implementación.\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 VM ID: {vmid}\n"
            f"📡 Estado: Consultando...\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
        )
