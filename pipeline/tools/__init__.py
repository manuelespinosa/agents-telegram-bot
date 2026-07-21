"""Catalog GateTools builders — ActionGate only (HITL propose path)."""
from __future__ import annotations

from typing import Any

from pipeline.tools.proxmox_tools import (
    ListVmsTool,
    SnapshotCreateTool,
    SnapshotListTool,
    VmRebootTool,
    VmStartTool,
    VmStatusTool,
    VmStopTool,
)
from pipeline.tools.uptime_tools import ServiceUptimeAllTool, ServiceUptimeTool

__all__ = [
    "build_worker_tools",
    "build_read_tools",
    "build_all_tools",
    "ListVmsTool",
    "VmStatusTool",
    "VmStartTool",
    "VmStopTool",
    "VmRebootTool",
    "SnapshotListTool",
    "SnapshotCreateTool",
    "ServiceUptimeTool",
    "ServiceUptimeAllTool",
]


def _ctor_kwargs(
    gate: Any,
    actor: str,
    pending_collector: list[Any] | None,
) -> dict[str, Any]:
    return {
        "gate": gate,
        "actor": actor,
        "pending_collector": pending_collector,
    }


def build_read_tools(
    gate: Any,
    actor: str = "pipeline:worker",
    pending_collector: list[Any] | None = None,
) -> list[Any]:
    """Read-only tools (safe when budget blocks mutations)."""
    kw = _ctor_kwargs(gate, actor, pending_collector)
    return [
        ListVmsTool(**kw),
        VmStatusTool(**kw),
        SnapshotListTool(**kw),
        ServiceUptimeTool(**kw),
        ServiceUptimeAllTool(**kw),
    ]


def build_worker_tools(
    gate: Any,
    actor: str = "pipeline:worker",
    pending_collector: list[Any] | None = None,
) -> list[Any]:
    """Full worker toolset: reads + write proposes via ActionGate."""
    kw = _ctor_kwargs(gate, actor, pending_collector)
    return [
        *build_read_tools(gate, actor, pending_collector),
        VmStartTool(**kw),
        VmStopTool(**kw),
        VmRebootTool(**kw),
        SnapshotCreateTool(**kw),
    ]


def build_all_tools(
    gate: Any,
    actor: str = "pipeline:crisis",
    pending_collector: list[Any] | None = None,
) -> list[Any]:
    """Crisis tool list — same catalog GateTools as worker (D-10)."""
    return build_worker_tools(gate, actor=actor, pending_collector=pending_collector)
