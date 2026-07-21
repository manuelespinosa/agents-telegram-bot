"""Proxmox lifecycle + snapshot GateTools (one BaseTool per ActionId)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from action_catalog import SnapshotCreateParams, VmIdParams
from pipeline.tools.base import GateTool


class VmIdToolInput(VmIdParams):
    """VmIdParams + reason for agent tool calls."""

    reason: str = Field(
        default="operator request",
        description="Why this action is needed",
    )


class SnapshotCreateToolInput(SnapshotCreateParams):
    """SnapshotCreateParams + reason."""

    reason: str = Field(
        default="operator request",
        description="Why this snapshot is needed",
    )


class EmptyToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        default="operator request",
        description="Why this action is needed",
    )


class ListVmsTool(GateTool):
    name: str = "list_vms"
    description: str = "List cluster VMs/CTs (read-only)."
    args_schema: type[BaseModel] = EmptyToolInput
    action_id: str = "list_vms"

    def _run(self, reason: str = "operator request", **_: Any) -> str:
        return self.propose({}, reason=reason)


class VmStatusTool(GateTool):
    name: str = "vm_status"
    description: str = "Get current status for a VM/CT by vmid (read-only)."
    args_schema: type[BaseModel] = VmIdToolInput
    action_id: str = "vm_status"

    def _run(
        self,
        vmid: int,
        node: str | None = None,
        reason: str = "operator request",
        **_: Any,
    ) -> str:
        params: dict[str, Any] = {"vmid": int(vmid)}
        if node:
            params["node"] = node
        return self.propose(params, reason=reason)


class VmStartTool(GateTool):
    name: str = "vm_start"
    description: str = (
        "Propose starting a QEMU/LXC guest. Write ops require human approval."
    )
    args_schema: type[BaseModel] = VmIdToolInput
    action_id: str = "vm_start"

    def _run(
        self,
        vmid: int,
        node: str | None = None,
        reason: str = "operator request",
        **_: Any,
    ) -> str:
        params: dict[str, Any] = {"vmid": int(vmid)}
        if node:
            params["node"] = node
        return self.propose(params, reason=reason)


class VmStopTool(GateTool):
    name: str = "vm_stop"
    description: str = (
        "Propose stopping a QEMU/LXC guest. Write ops require human approval."
    )
    args_schema: type[BaseModel] = VmIdToolInput
    action_id: str = "vm_stop"

    def _run(
        self,
        vmid: int,
        node: str | None = None,
        reason: str = "operator request",
        **_: Any,
    ) -> str:
        params: dict[str, Any] = {"vmid": int(vmid)}
        if node:
            params["node"] = node
        return self.propose(params, reason=reason)


class VmRebootTool(GateTool):
    name: str = "vm_reboot"
    description: str = (
        "Propose rebooting a QEMU/LXC guest. Write ops require human approval."
    )
    args_schema: type[BaseModel] = VmIdToolInput
    action_id: str = "vm_reboot"

    def _run(
        self,
        vmid: int,
        node: str | None = None,
        reason: str = "operator request",
        **_: Any,
    ) -> str:
        params: dict[str, Any] = {"vmid": int(vmid)}
        if node:
            params["node"] = node
        return self.propose(params, reason=reason)


class SnapshotListTool(GateTool):
    name: str = "snapshot_list"
    description: str = "List Proxmox snapshots for a VM/CT (read-only)."
    args_schema: type[BaseModel] = VmIdToolInput
    action_id: str = "snapshot_list"

    def _run(
        self,
        vmid: int,
        node: str | None = None,
        reason: str = "operator request",
        **_: Any,
    ) -> str:
        params: dict[str, Any] = {"vmid": int(vmid)}
        if node:
            params["node"] = node
        return self.propose(params, reason=reason)


class SnapshotCreateTool(GateTool):
    name: str = "snapshot_create"
    description: str = (
        "Propose creating a Proxmox snapshot (no rollback/delete). Requires approval."
    )
    args_schema: type[BaseModel] = SnapshotCreateToolInput
    action_id: str = "snapshot_create"

    def _run(
        self,
        vmid: int,
        snapname: str,
        description: str = "",
        reason: str = "operator request",
        **_: Any,
    ) -> str:
        return self.propose(
            {
                "vmid": int(vmid),
                "snapname": snapname,
                "description": description or "",
            },
            reason=reason,
        )
