"""Curated service uptime GateTools (read-only probes via ActionGate)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from action_catalog import ServiceNameParams
from pipeline.tools.base import GateTool
from pipeline.tools.proxmox_tools import EmptyToolInput


class ServiceUptimeToolInput(ServiceNameParams):
    reason: str = Field(
        default="operator request",
        description="Why this probe is needed",
    )


class ServiceUptimeTool(GateTool):
    name: str = "service_uptime"
    description: str = (
        "Probe one curated service (HTTP/TCP) by name from allowlist (read-only)."
    )
    args_schema: type[BaseModel] = ServiceUptimeToolInput
    action_id: str = "service_uptime"

    def _run(
        self,
        service_name: str,
        reason: str = "operator request",
        **_: Any,
    ) -> str:
        return self.propose({"service_name": service_name}, reason=reason)


class ServiceUptimeAllTool(GateTool):
    name: str = "service_uptime_all"
    description: str = "Probe all enabled curated services (read-only)."
    args_schema: type[BaseModel] = EmptyToolInput
    action_id: str = "service_uptime_all"

    def _run(self, reason: str = "operator request", **_: Any) -> str:
        return self.propose({}, reason=reason)
