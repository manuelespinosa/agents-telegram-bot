"""Typed Action Catalog allowlist (HITL-01 / Phase 4 seed). Pure Python — no PTB/Proxmox imports."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class UnknownActionError(ValueError):
    """Raised when action_id is outside the executable CATALOG (hard block)."""

    def __init__(self, action_id: str):
        self.action_id = action_id
        super().__init__(f"Unknown action: {action_id}")


class RiskTier(str, Enum):
    READ = "read"
    WRITE = "write"
    CRISIS = "crisis"
    DESTRUCTIVE = "destructive"  # reserved; not in seed registry (D-11)


class ActionId(str, Enum):
    LIST_VMS = "list_vms"
    VM_STATUS = "vm_status"
    VM_START = "vm_start"
    VM_STOP = "vm_stop"
    VM_REBOOT = "vm_reboot"
    SNAPSHOT_LIST = "snapshot_list"
    SNAPSHOT_CREATE = "snapshot_create"
    SERVICE_UPTIME = "service_uptime"
    SERVICE_UPTIME_ALL = "service_uptime_all"
    CRISIS_PROBE = "crisis_probe"  # optional gate stub; no Proxmox mapping (D-10)
    # D-06: snapshot_rollback / snapshot_delete intentionally absent
    # D-08: no SSH / Ansible / free-shell ActionIds


class EmptyParams(BaseModel):
    """Params model that accepts only empty / no extra fields."""

    model_config = ConfigDict(extra="forbid")


class VmIdParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vmid: int = Field(..., ge=100, le=999_999_999)
    node: str | None = None


class SnapshotCreateParams(BaseModel):
    """Create snapshot params (D-05/D-06). Snapname pattern limits blast surface."""

    model_config = ConfigDict(extra="forbid")

    vmid: int = Field(..., ge=100, le=999_999_999)
    snapname: str = Field(
        ...,
        min_length=1,
        max_length=40,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    description: str = ""


class ServiceNameParams(BaseModel):
    """Service uptime params — non-empty name only; curated membership enforced in executor (D-07)."""

    model_config = ConfigDict(extra="forbid")

    service_name: str = Field(..., min_length=1, max_length=64)


class ActionDef(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: ActionId
    tier: RiskTier
    params_model: type[BaseModel]
    description: str
    expected_impact: str
    requires_deepseek: bool = False


# Sole executable registry — source of truth (D-02). No free-form shell.
CATALOG: dict[ActionId, ActionDef] = {
    ActionId.LIST_VMS: ActionDef(
        id=ActionId.LIST_VMS,
        tier=RiskTier.READ,
        params_model=EmptyParams,
        description="List cluster VMs",
        expected_impact="None (read-only)",
    ),
    ActionId.VM_STATUS: ActionDef(
        id=ActionId.VM_STATUS,
        tier=RiskTier.READ,
        params_model=VmIdParams,
        description="VM current status",
        expected_impact="None (read-only)",
    ),
    ActionId.VM_START: ActionDef(
        id=ActionId.VM_START,
        tier=RiskTier.WRITE,
        params_model=VmIdParams,
        description="Start QEMU/LXC",
        expected_impact="VM powers on; services become reachable",
    ),
    ActionId.VM_STOP: ActionDef(
        id=ActionId.VM_STOP,
        tier=RiskTier.WRITE,
        params_model=VmIdParams,
        description="Stop QEMU/LXC",
        expected_impact="VM powers off; downtime until start",
    ),
    ActionId.VM_REBOOT: ActionDef(
        id=ActionId.VM_REBOOT,
        tier=RiskTier.WRITE,
        params_model=VmIdParams,
        description="Reboot QEMU/LXC",
        expected_impact="Brief downtime during reboot",
    ),
    ActionId.SNAPSHOT_LIST: ActionDef(
        id=ActionId.SNAPSHOT_LIST,
        tier=RiskTier.READ,
        params_model=VmIdParams,
        description="List Proxmox snapshots for a VM/CT",
        expected_impact="None (read-only)",
    ),
    ActionId.SNAPSHOT_CREATE: ActionDef(
        id=ActionId.SNAPSHOT_CREATE,
        tier=RiskTier.WRITE,
        params_model=SnapshotCreateParams,
        description="Create Proxmox snapshot (no rollback/delete in seed)",
        expected_impact="New snapshot disk usage; no state rollback",
    ),
    ActionId.SERVICE_UPTIME: ActionDef(
        id=ActionId.SERVICE_UPTIME,
        tier=RiskTier.READ,
        params_model=ServiceNameParams,
        description="Probe one curated service (HTTP/TCP)",
        expected_impact="None (read-only local probe)",
    ),
    ActionId.SERVICE_UPTIME_ALL: ActionDef(
        id=ActionId.SERVICE_UPTIME_ALL,
        tier=RiskTier.READ,
        params_model=EmptyParams,
        description="Probe all enabled curated services",
        expected_impact="None (read-only local probes)",
    ),
    ActionId.CRISIS_PROBE: ActionDef(
        id=ActionId.CRISIS_PROBE,
        tier=RiskTier.CRISIS,
        params_model=EmptyParams,
        description="Crisis gate probe (stub; no DeepSeek call in Phase 3)",
        expected_impact="None — gate + requires_deepseek flag only (D-10)",
        requires_deepseek=True,
    ),
}


def resolve(action_id: str, raw_params: dict[str, Any] | None) -> ActionDef:
    """Validate action_id membership and params; raise UnknownActionError if outside catalog."""
    try:
        aid = ActionId(action_id)
    except ValueError as e:
        raise UnknownActionError(action_id) from e
    if aid not in CATALOG:
        raise UnknownActionError(action_id)
    defn = CATALOG[aid]
    defn.params_model.model_validate(raw_params or {})
    return defn
