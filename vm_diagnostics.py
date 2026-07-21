"""On-demand VM diagnostics (MON-04).

Read-only Proxmox paths for QEMU and LXC:
  - cluster.resources type=vm → node + type
  - nodes(node).qemu|lxc(vmid).status.current
  - nodes(node).qemu|lxc(vmid).snapshot

CPU% = cpu * 100 (fraction). snaptime = Unix seconds unless value > 1e12 (ms).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from proxmoxer import ProxmoxAPI

from config import settings

logger = logging.getLogger(__name__)


def _format_bytes(n: float | int | None) -> str:
    if n is None:
        return "N/A"
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "N/A"
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024.0
        i += 1
    return f"{n:.1f} {units[i]}"


def _format_uptime(seconds: float | int | None) -> str:
    if seconds is None:
        return "N/A"
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return "N/A"
    days, rem = divmod(s, 86400)
    hours, rem = divmod(rem, 3600)
    mins, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {mins}m"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def format_snaptime(snaptime: Any) -> str:
    """Format Proxmox snaptime: seconds, or ms if value > 1e12."""
    if snaptime is None:
        return "N/A"
    try:
        val = float(snaptime)
    except (TypeError, ValueError):
        return str(snaptime)
    if val > 1e12:
        val = val / 1000.0
    try:
        dt = datetime.fromtimestamp(val, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except (OverflowError, OSError, ValueError):
        return str(int(val))


class VMDiagnostics:
    """Diagnóstico read-only de máquinas virtuales / contenedores."""

    def __init__(self, proxmox: Any | None = None):
        self._proxmox = proxmox

    @property
    def proxmox(self) -> Any:
        if self._proxmox is None:
            self._proxmox = ProxmoxAPI(
                settings.proxmox_host,
                user=settings.proxmox_user,
                token_name=settings.proxmox_token_name,
                token_value=settings.proxmox_token_value,
                verify_ssl=settings.proxmox_verify_ssl,
            )
        return self._proxmox

    def _diagnose_sync(self, vmid: int) -> str:
        vms = self.proxmox.cluster.resources.get(type="vm") or []
        match = None
        for vm in vms:
            try:
                if int(vm.get("vmid")) == int(vmid):
                    match = vm
                    break
            except (TypeError, ValueError):
                continue

        if match is None:
            return (
                f"❌ VM/CT {vmid} no encontrada en el clúster.\n"
                "Verifica el ID en Proxmox (unknown / not found)."
            )

        node = match.get("node") or "?"
        vtype = (match.get("type") or "qemu").lower()
        name = match.get("name") or ""
        status_cluster = match.get("status") or "?"

        # Branch qemu vs lxc for detailed status + snapshots
        try:
            if vtype == "lxc":
                api = self.proxmox.nodes(node).lxc(vmid)
            else:
                api = self.proxmox.nodes(node).qemu(vmid)
                vtype = "qemu"
            status = api.status.current.get() or {}
        except Exception as e:
            logger.error("status.current failed vmid=%s: %s", vmid, e)
            status = {}

        cpu = status.get("cpu", match.get("cpu", 0))
        try:
            cpu_pct = float(cpu or 0) * 100.0
        except (TypeError, ValueError):
            cpu_pct = 0.0
        mem = status.get("mem", match.get("mem", 0)) or 0
        maxmem = status.get("maxmem", match.get("maxmem", 0)) or 0
        try:
            ram_pct = (float(mem) / float(maxmem) * 100.0) if maxmem else 0.0
        except (TypeError, ValueError, ZeroDivisionError):
            ram_pct = 0.0
        uptime = status.get("uptime", match.get("uptime"))
        running = status.get("status") or status_cluster

        # Agent-related field (QEMU guest agent / LXC has no qemu-agent)
        if vtype == "qemu":
            agent = status.get("agent")
            if agent is None:
                # Some PVE versions put agent under different keys
                agent = status.get("qmpstatus") or status.get("agent-enabled")
            agent_txt = (
                str(agent)
                if agent is not None
                else "desconocido (consulta status/agent)"
            )
        else:
            agent_txt = "N/A (LXC — sin QEMU guest agent)"

        # Snapshots
        snap_lines: list[str] = []
        try:
            if vtype == "lxc":
                snaps = self.proxmox.nodes(node).lxc(vmid).snapshot.get() or []
            else:
                snaps = self.proxmox.nodes(node).qemu(vmid).snapshot.get() or []
            for snap in snaps:
                sname = snap.get("name") or "?"
                if sname == "current":
                    continue
                st = format_snaptime(snap.get("snaptime"))
                desc = snap.get("description") or ""
                snap_lines.append(f"  • {sname}  {st}  {desc}".rstrip())
        except Exception as e:
            logger.warning("snapshots failed vmid=%s: %s", vmid, e)
            snap_lines.append("  ⚠️ No se pudieron listar snapshots")

        if not snap_lines:
            snap_lines.append("  (sin snapshots)")

        lines = [
            f"🔍 Diagnóstico VM/CT {vmid}",
            f"Nombre: {name}",
            f"Tipo: {vtype}  Nodo: {node}  Estado: {running}",
            f"CPU: {cpu_pct:.1f}%",
            f"RAM: {ram_pct:.1f}%  ({_format_bytes(mem)} / {_format_bytes(maxmem)})",
            f"Uptime: {_format_uptime(uptime)}",
            f"Agent: {agent_txt}",
            "Snapshots:",
            *snap_lines,
        ]
        return "\n".join(lines)

    async def diagnose(self, vmid: int) -> str:
        """Obtener diagnóstico completo de una VM/CT (read-only)."""
        return await asyncio.to_thread(self._diagnose_sync, int(vmid))
