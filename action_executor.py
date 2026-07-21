"""Real Proxmox ActionExecutor (HITL-03 / D-12 / Phase 4 seed).

Read + write lifecycle via proxmoxer. Snapshots create/list. Curated uptime probes.
No Telegram imports. No SSH or free-shell (D-08).
Write paths: vm_start | vm_stop | vm_reboot | snapshot_create.
"""
from __future__ import annotations

import logging
import socket
from pathlib import Path
from typing import Any, Callable

import httpx
from proxmoxer import ProxmoxAPI

from config import Settings, settings as default_settings

logger = logging.getLogger(__name__)

WRITE_ACTIONS = frozenset({"vm_start", "vm_stop", "vm_reboot", "snapshot_create"})
READ_ACTIONS = frozenset(
    {
        "list_vms",
        "vm_status",
        "snapshot_list",
        "service_uptime",
        "service_uptime_all",
    }
)

PROBE_TIMEOUT_SEC = 3.0


class UnsupportedActionError(ValueError):
    """Raised when action_id has no executor mapping (e.g. crisis_probe)."""

    def __init__(self, action_id: str):
        self.action_id = action_id
        super().__init__(f"No executor mapping for action: {action_id}")


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


def _coerce_yaml_scalar(raw: str) -> Any:
    s = raw.strip()
    if not s:
        return ""
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "~", "none"):
        return None
    try:
        if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
            return int(s)
    except ValueError:
        pass
    return s


def parse_uptime_services_yaml(text: str) -> list[dict[str, Any]]:
    """Parse curated uptime YAML (list of maps). Prefer PyYAML; fallback for seed shape."""
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
        if data is None:
            return []
        if not isinstance(data, list):
            raise ValueError("uptime_services.yaml must be a list of service objects")
        return [dict(item) for item in data if isinstance(item, dict)]
    except ImportError:
        pass

    services: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.lstrip()
        if stripped.startswith("- "):
            if current:
                services.append(current)
            current = {}
            rest = stripped[2:].strip()
            if rest and ":" in rest:
                key, val = rest.split(":", 1)
                current[key.strip()] = _coerce_yaml_scalar(val)
        elif current is not None and ":" in stripped:
            key, val = stripped.split(":", 1)
            current[key.strip()] = _coerce_yaml_scalar(val)
    if current:
        services.append(current)
    return services


def load_uptime_services(path: str | Path) -> list[dict[str, Any]]:
    """Load curated service list from YAML path (D-07)."""
    p = Path(path)
    if not p.is_file():
        logger.warning("uptime services file missing: %s", p)
        return []
    return parse_uptime_services_yaml(p.read_text(encoding="utf-8"))


class ActionExecutor:
    """Proxmox lifecycle + snapshot + curated uptime executor injected into ActionGate."""

    def __init__(
        self,
        proxmox: Any | None = None,
        settings: Settings | None = None,
        uptime_services: list[dict[str, Any]] | None = None,
        uptime_loader: Callable[[str], list[dict[str, Any]]] | None = None,
    ):
        self._proxmox = proxmox
        self._settings = settings or default_settings
        self._uptime_services_override = uptime_services
        self._uptime_loader = uptime_loader or load_uptime_services

    @property
    def proxmox(self) -> Any:
        if self._proxmox is None:
            self._proxmox = ProxmoxAPI(
                self._settings.proxmox_host,
                user=self._settings.proxmox_user,
                token_name=self._settings.proxmox_token_name,
                token_value=self._settings.proxmox_token_value,
                verify_ssl=self._settings.proxmox_verify_ssl,
            )
        return self._proxmox

    def get_uptime_services(self) -> list[dict[str, Any]]:
        """Return curated uptime definitions (injectable override for tests)."""
        if self._uptime_services_override is not None:
            return self._uptime_services_override
        return self._uptime_loader(self._settings.uptime_services_path)

    def resolve_vm(self, vmid: int) -> tuple[str, str]:
        """Return (node, kind) for vmid from cluster.resources type=vm.

        kind is 'qemu' or 'lxc'. Raises LookupError if not found.
        """
        target = int(vmid)
        resources = self.proxmox.cluster.resources.get(type="vm") or []
        for vm in resources:
            try:
                if int(vm.get("vmid")) == target:
                    node = vm.get("node")
                    kind = (vm.get("type") or "qemu").lower()
                    if not node:
                        raise LookupError(f"VM {target} has no node")
                    if kind not in ("qemu", "lxc"):
                        kind = "qemu"
                    return str(node), kind
            except (TypeError, ValueError):
                continue
        raise LookupError(f"VM {target} not found in cluster")

    def _guest_api(self, node: str, kind: str, vmid: int) -> Any:
        if kind == "lxc":
            return self.proxmox.nodes(node).lxc(vmid)
        return self.proxmox.nodes(node).qemu(vmid)

    def execute_read(self, action_id: str, params: dict[str, Any]) -> str:
        """Execute cataloged read actions; never POST power status."""
        if action_id not in READ_ACTIONS:
            raise UnsupportedActionError(action_id)
        if action_id == "list_vms":
            return self._list_vms()
        if action_id == "vm_status":
            return self._vm_status(int(params["vmid"]))
        if action_id == "snapshot_list":
            return self._snapshot_list(int(params["vmid"]))
        if action_id == "service_uptime":
            return self._service_uptime(str(params["service_name"]))
        if action_id == "service_uptime_all":
            return self._service_uptime_all()
        raise UnsupportedActionError(action_id)

    def execute_write(self, action_id: str, params: dict[str, Any]) -> str:
        """Execute cataloged write actions only (D-12). No free-form shell."""
        if action_id not in WRITE_ACTIONS:
            raise UnsupportedActionError(action_id)
        if action_id == "snapshot_create":
            if "vmid" not in params or "snapname" not in params:
                raise KeyError("vmid and snapname required for snapshot_create")
            return self._snapshot_create(
                int(params["vmid"]),
                str(params["snapname"]),
                str(params.get("description") or ""),
            )

        if "vmid" not in params:
            raise KeyError("vmid required for write action")
        vmid = int(params["vmid"])
        node, kind = self.resolve_vm(vmid)
        api = self._guest_api(node, kind, vmid)

        if action_id == "vm_start":
            upid = api.status.start.post()
            verb = "start"
        elif action_id == "vm_stop":
            upid = api.status.stop.post()
            verb = "stop"
        else:  # vm_reboot
            upid = api.status.reboot.post()
            verb = "reboot"

        logger.info(
            "Proxmox %s vmid=%s node=%s kind=%s upid=%s",
            verb,
            vmid,
            node,
            kind,
            upid,
        )
        return (
            f"✅ {verb} enviado para {kind} {vmid} @ {node}\n"
            f"UPID/task: {upid}"
        )

    def _list_vms(self) -> str:
        resources = self.proxmox.cluster.resources.get(type="vm") or []
        if not resources:
            return "📋 Inventario VMs/CTs\n\n(sin recursos type=vm)"

        # Sort by vmid for stable plain-text output
        rows: list[tuple[int, str, str, str, str]] = []
        for vm in resources:
            try:
                vmid = int(vm.get("vmid"))
            except (TypeError, ValueError):
                continue
            name = str(vm.get("name") or "")
            kind = str(vm.get("type") or "?")
            node = str(vm.get("node") or "?")
            status = str(vm.get("status") or "?")
            rows.append((vmid, name, kind, node, status))
        rows.sort(key=lambda r: r[0])

        lines = ["📋 Inventario VMs/CTs", ""]
        for vmid, name, kind, node, status in rows:
            label = name or "(sin nombre)"
            lines.append(f"• {vmid}  {label}  [{kind}]  {node}  {status}")
        lines.append("")
        lines.append(f"Total: {len(rows)}")
        return "\n".join(lines)

    def _vm_status(self, vmid: int) -> str:
        node, kind = self.resolve_vm(vmid)
        api = self._guest_api(node, kind, vmid)
        status = api.status.current.get() or {}
        name = status.get("name") or ""
        running = status.get("status") or "?"
        cpu = status.get("cpu", 0)
        try:
            cpu_pct = float(cpu or 0) * 100.0
        except (TypeError, ValueError):
            cpu_pct = 0.0
        mem = status.get("mem", 0) or 0
        maxmem = status.get("maxmem", 0) or 0
        try:
            ram_pct = (float(mem) / float(maxmem) * 100.0) if maxmem else 0.0
        except (TypeError, ValueError, ZeroDivisionError):
            ram_pct = 0.0
        uptime = status.get("uptime")

        lines = [
            f"📊 Estado VM/CT {vmid}",
            f"Nombre: {name or '(n/a)'}",
            f"Tipo: {kind}  Nodo: {node}  Estado: {running}",
            f"CPU: {cpu_pct:.1f}%",
            f"RAM: {ram_pct:.1f}%  ({_format_bytes(mem)} / {_format_bytes(maxmem)})",
            f"Uptime: {_format_uptime(uptime)}",
        ]
        return "\n".join(lines)

    def _snapshot_list(self, vmid: int) -> str:
        """List snapshots for vmid; skip Proxmox synthetic name 'current'."""
        node, kind = self.resolve_vm(vmid)
        api = self._guest_api(node, kind, vmid)
        snaps = api.snapshot.get() or []
        lines = [f"📸 Snapshots {kind} {vmid} @ {node}", ""]
        count = 0
        for snap in snaps:
            sname = str(snap.get("name") or "?")
            if sname == "current":
                continue
            desc = str(snap.get("description") or "").strip()
            if desc:
                lines.append(f"• {sname}  {desc}")
            else:
                lines.append(f"• {sname}")
            count += 1
        if count == 0:
            lines.append("(sin snapshots)")
        return "\n".join(lines)

    def _snapshot_create(self, vmid: int, snapname: str, description: str = "") -> str:
        """Create snapshot via proxmoxer guest.snapshot.post (D-05/D-06)."""
        node, kind = self.resolve_vm(vmid)
        api = self._guest_api(node, kind, vmid)
        upid = api.snapshot.post(snapname=snapname, description=description or "")
        logger.info(
            "Proxmox snapshot_create vmid=%s snapname=%s node=%s kind=%s upid=%s",
            vmid,
            snapname,
            node,
            kind,
            upid,
        )
        return (
            f"✅ snapshot_create {snapname} en {kind} {vmid} @ {node}\n"
            f"UPID: {upid}"
        )

    def _find_service(self, service_name: str) -> dict[str, Any] | None:
        name = service_name.strip().lower()
        for svc in self.get_uptime_services():
            svc_name = str(svc.get("name") or "").strip().lower()
            if svc_name == name:
                return svc
        return None

    def _service_uptime(self, service_name: str) -> str:
        """Probe one curated service; unknown names hard-reject (D-07 / T-04-02)."""
        svc = self._find_service(service_name)
        if svc is None:
            return (
                f"❌ service_uptime: unknown service '{service_name}' "
                f"(not in curated allowlist)"
            )
        return self._probe_service(svc)

    def _service_uptime_all(self) -> str:
        """Probe only curated services with enabled=true."""
        services = self.get_uptime_services()
        enabled = [s for s in services if bool(s.get("enabled"))]
        if not enabled:
            return "📡 Service uptime\n\n(no enabled services in curated list)"
        lines = ["📡 Service uptime", ""]
        for svc in enabled:
            lines.append(self._probe_service(svc))
        return "\n".join(lines)

    def _probe_service(self, svc: dict[str, Any]) -> str:
        name = str(svc.get("name") or "?")
        probe = str(svc.get("probe") or "http").lower()
        try:
            if probe == "tcp":
                host = str(svc.get("host") or "")
                port = int(svc.get("port") or 0)
                if not host or not port:
                    return f"• {name}: DOWN (misconfigured tcp host/port)"
                ok = self._probe_tcp(host, port)
            else:
                url = str(svc.get("url") or "")
                if not url:
                    return f"• {name}: DOWN (misconfigured http url)"
                ok = self._probe_http(url)
            state = "UP" if ok else "DOWN"
            return f"• {name}: {state}"
        except Exception as e:  # plain error type only (T-04-04)
            err = type(e).__name__
            logger.warning("uptime probe failed name=%s: %s", name, err)
            return f"• {name}: DOWN ({err})"

    def _probe_http(self, url: str) -> bool:
        with httpx.Client(
            timeout=PROBE_TIMEOUT_SEC,
            follow_redirects=True,
            verify=False,
        ) as client:
            resp = client.get(url)
            return 200 <= resp.status_code < 400

    def _probe_tcp(self, host: str, port: int) -> bool:
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT_SEC):
            return True
