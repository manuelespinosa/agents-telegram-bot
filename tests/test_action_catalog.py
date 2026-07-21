"""HITL-01: typed Action Catalog allowlist unit tests."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from action_catalog import (
    ActionId,
    RiskTier,
    CATALOG,
    UnknownActionError,
    resolve,
)


def test_resolve_list_vms_read():
    defn = resolve("list_vms", {})
    assert defn.id == ActionId.LIST_VMS
    assert defn.tier == RiskTier.READ
    assert defn.requires_deepseek is False


def test_resolve_vm_status_valid_vmid():
    defn = resolve("vm_status", {"vmid": 100})
    assert defn.id == ActionId.VM_STATUS
    assert defn.tier == RiskTier.READ


def test_resolve_vm_status_invalid_vmid_rejected():
    with pytest.raises(ValidationError):
        resolve("vm_status", {"vmid": 1})


@pytest.mark.parametrize(
    "action_id",
    ["vm_start", "vm_stop", "vm_reboot"],
)
def test_resolve_write_actions(action_id: str):
    defn = resolve(action_id, {"vmid": 101})
    assert defn.tier == RiskTier.WRITE
    assert defn.requires_deepseek is False


def test_unknown_action_hard_block():
    with pytest.raises(UnknownActionError) as exc:
        resolve("vm_destroy", {"vmid": 100})
    assert "vm_destroy" in str(exc.value)


def test_destructive_tier_exists_but_not_in_seed_catalog():
    assert RiskTier.DESTRUCTIVE == "destructive"
    assert all(d.tier != RiskTier.DESTRUCTIVE for d in CATALOG.values())


def test_seed_catalog_has_five_proxmox_lifecycle_actions():
    seed_ids = {
        ActionId.LIST_VMS,
        ActionId.VM_STATUS,
        ActionId.VM_START,
        ActionId.VM_STOP,
        ActionId.VM_REBOOT,
    }
    assert seed_ids.issubset(set(CATALOG.keys()))


def test_crisis_probe_optional_stub():
    """If crisis_probe is registered, it must be crisis tier with deepseek flag."""
    if ActionId.CRISIS_PROBE in CATALOG:
        defn = resolve("crisis_probe", {})
        assert defn.tier == RiskTier.CRISIS
        assert defn.requires_deepseek is True


# --- Phase 4 seed: snapshots + curated uptime (D-05/D-06/D-07) ---


def test_resolve_snapshot_list_read():
    defn = resolve("snapshot_list", {"vmid": 300})
    assert defn.id == ActionId.SNAPSHOT_LIST
    assert defn.tier == RiskTier.READ


def test_resolve_snapshot_create_write():
    defn = resolve("snapshot_create", {"vmid": 300, "snapname": "ai-test"})
    assert defn.id == ActionId.SNAPSHOT_CREATE
    assert defn.tier == RiskTier.WRITE


def test_resolve_snapshot_create_invalid_snapname():
    with pytest.raises(ValidationError):
        resolve("snapshot_create", {"vmid": 300, "snapname": "bad name!"})


def test_resolve_service_uptime_read():
    defn = resolve("service_uptime", {"service_name": "jellyfin"})
    assert defn.id == ActionId.SERVICE_UPTIME
    assert defn.tier == RiskTier.READ


def test_resolve_service_uptime_empty_name_rejected():
    with pytest.raises(ValidationError):
        resolve("service_uptime", {"service_name": ""})


def test_resolve_service_uptime_all_read():
    defn = resolve("service_uptime_all", {})
    assert defn.id == ActionId.SERVICE_UPTIME_ALL
    assert defn.tier == RiskTier.READ


@pytest.mark.parametrize("action_id", ["snapshot_rollback", "snapshot_delete"])
def test_snapshot_mutations_not_in_seed_hard_block(action_id: str):
    """D-06: rollback/delete are not catalog ActionIds."""
    with pytest.raises(UnknownActionError) as exc:
        resolve(action_id, {})
    assert action_id in str(exc.value)


def test_no_ssh_action_ids_in_catalog():
    """D-08: no SSH/Ansible/paramiko ActionIds in seed."""
    ssh_like = {
        "ssh_exec",
        "ssh_run",
        "ansible_playbook",
        "shell",
        "free_shell",
        "paramiko",
    }
    catalog_values = {a.value for a in CATALOG.keys()}
    assert catalog_values.isdisjoint(ssh_like)
