"""Homelab health aggregate report (MON-03).

Collects read-only Proxmox nodes/VMs/storage + Docker critical events summary.
CPU display: Proxmox ``cpu`` is a 0.0–1.0 fraction → show as ``cpu * 100``
(do NOT divide by maxcpu).

MON-01 v1: Proxmox API + Docker logs only; journalctl deferred.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from proxmoxer import ProxmoxAPI

from config import settings
from log_crawler import LogCrawler
from report_format import split_message

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
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


class HealthReporter:
    """Recolecta y formatea datos de salud del homelab (read-only)."""

    def __init__(
        self,
        proxmox: Any | None = None,
        log_crawler: LogCrawler | None = None,
    ):
        self._proxmox = proxmox
        self.log_crawler = log_crawler or LogCrawler()

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

    def _format_pct(self, used: float, total: float) -> str:
        if total is None or total <= 0:
            return "N/A"
        try:
            pct = (float(used) / float(total)) * 100
        except (TypeError, ValueError, ZeroDivisionError):
            return "N/A"
        bar = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
        if pct > 90:
            return f"🔴 {bar} {pct:.0f}%"
        if pct > 70:
            return f"🟡 {bar} {pct:.0f}%"
        return f"🟢 {bar} {pct:.0f}%"

    def _cpu_pct(self, cpu: Any) -> float:
        """Proxmox cpu is fraction 0–1 → percentage."""
        try:
            return float(cpu or 0) * 100.0
        except (TypeError, ValueError):
            return 0.0

    def _collect_nodes_sync(self) -> str:
        lines = ["📡 Nodos Proxmox"]
        nodes = self.proxmox.nodes.get()
        if not nodes:
            lines.append("  (sin nodos)")
            return "\n".join(lines)
        for node in nodes:
            name = node.get("node") or node.get("id") or "?"
            status = node.get("status") or "?"
            cpu_pct = self._cpu_pct(node.get("cpu"))
            mem = node.get("mem") or 0
            maxmem = node.get("maxmem") or 0
            uptime = _format_uptime(node.get("uptime"))
            mem_bar = self._format_pct(mem, maxmem)
            lines.append(
                f"  • {name} [{status}]  CPU {cpu_pct:.0f}%  "
                f"RAM {mem_bar}  up {uptime}"
            )
        return "\n".join(lines)

    def _collect_vms_sync(self) -> str:
        lines = ["🖥️ VMs / CTs"]
        vms = self.proxmox.cluster.resources.get(type="vm")
        if not vms:
            lines.append("  (ninguna)")
            return "\n".join(lines)
        # Sort by vmid for stable output
        try:
            vms = sorted(vms, key=lambda v: int(v.get("vmid") or 0))
        except Exception:
            pass
        for vm in vms:
            vmid = vm.get("vmid", "?")
            name = vm.get("name") or ""
            vtype = vm.get("type") or "?"
            status = vm.get("status") or "?"
            node = vm.get("node") or "?"
            cpu_pct = self._cpu_pct(vm.get("cpu"))
            mem = vm.get("mem") or 0
            maxmem = vm.get("maxmem") or 0
            mem_bar = self._format_pct(mem, maxmem)
            lines.append(
                f"  • {vmid} {name} ({vtype}@{node}) [{status}]  "
                f"CPU {cpu_pct:.0f}%  RAM {mem_bar}"
            )
        return "\n".join(lines)

    def _collect_storage_sync(self) -> str:
        lines = ["💾 Storage"]
        nodes = self.proxmox.nodes.get()
        any_storage = False
        for node in nodes or []:
            nname = node.get("node") or "?"
            try:
                stores = self.proxmox.nodes(nname).storage.get()
            except Exception as e:
                lines.append(f"  • {nname}: error storage ({e})")
                continue
            for st in stores or []:
                # Skip inactive/disabled if flagged
                if st.get("active") in (0, False):
                    continue
                any_storage = True
                sname = st.get("storage") or st.get("name") or "?"
                used = st.get("used") or st.get("disk") or 0
                total = st.get("total") or st.get("maxdisk") or 0
                bar = self._format_pct(used, total)
                lines.append(
                    f"  • {nname}/{sname}: {bar} "
                    f"({_format_bytes(used)} / {_format_bytes(total)})"
                )
        if not any_storage and len(lines) == 1:
            lines.append("  (sin storage reportado)")
        return "\n".join(lines)

    async def _collect_docker_section(self) -> str:
        try:
            events = await self.log_crawler.get_critical_events(lines=100)
            summary = await self.log_crawler.summarize(events)
            return f"🐳 Eventos Docker\n{summary}"
        except Exception as e:
            logger.error("Docker events section failed: %s", e)
            return "🐳 Eventos Docker\n  ⚠️ No disponible (error de lectura)"

    async def collect_all_health(self) -> str:
        """Recolectar todos los datos de salud y devolver reporte formateado."""
        sections: list[str] = ["📊 Estado del Homelab"]

        try:
            nodes_txt = await asyncio.to_thread(self._collect_nodes_sync)
            sections.append(nodes_txt)
        except Exception as e:
            logger.error("nodes section failed: %s", e)
            sections.append("📡 Nodos Proxmox\n  ⚠️ Error al consultar nodos")

        try:
            vms_txt = await asyncio.to_thread(self._collect_vms_sync)
            sections.append(vms_txt)
        except Exception as e:
            logger.error("vms section failed: %s", e)
            sections.append("🖥️ VMs / CTs\n  ⚠️ Error al consultar VMs")

        try:
            storage_txt = await asyncio.to_thread(self._collect_storage_sync)
            sections.append(storage_txt)
        except Exception as e:
            logger.error("storage section failed: %s", e)
            sections.append("💾 Storage\n  ⚠️ Error al consultar storage")

        sections.append(await self._collect_docker_section())

        return "\n\n".join(sections)

    def _recipient_ids(self, app: Any) -> list[int]:
        recipients: set[int] = set()
        chat = (settings.telegram_chat_id or "").strip()
        if chat:
            try:
                recipients.add(int(chat))
            except ValueError:
                logger.warning("TELEGRAM_CHAT_ID no es entero: %r", chat)
        store = None
        if app is not None:
            store = getattr(app, "bot_data", {}).get("chat_store")
        if store is not None:
            try:
                for cid in store.list_chats():
                    recipients.add(int(cid))
            except Exception as e:
                logger.error("ChatStore.list_chats failed: %s", e)
        return sorted(recipients)

    async def send_daily_report(self, app: Any) -> None:
        """Enviar reporte diario a TELEGRAM_CHAT_ID ∪ ChatStore recipients."""
        report = await self.collect_all_health()
        recipients = self._recipient_ids(app)
        if not recipients:
            logger.warning(
                "send_daily_report: no recipients "
                "(set TELEGRAM_CHAT_ID or authorize a chat)"
            )
            return
        bot = app.bot
        chunks = split_message(report)
        for chat_id in recipients:
            for chunk in chunks:
                try:
                    # Plain text — no Markdown on dynamic Proxmox names
                    await bot.send_message(chat_id=chat_id, text=chunk)
                except Exception as e:
                    logger.error(
                        "send_daily_report failed chat_id=%s: %s",
                        chat_id,
                        e,
                    )
        logger.info(
            "send_daily_report: sent %s chunk(s) to %s recipient(s)",
            len(chunks),
            len(recipients),
        )
