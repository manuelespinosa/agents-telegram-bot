"""ActionExecutor unit tests — mocked proxmoxer, no live cluster."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from action_executor import (
    READ_ACTIONS,
    WRITE_ACTIONS,
    ActionExecutor,
    UnsupportedActionError,
)


def _resource_entry(
    vmid: int,
    *,
    node: str = "pve",
    kind: str = "qemu",
    name: str = "test-vm",
    status: str = "running",
) -> dict:
    return {
        "vmid": vmid,
        "node": node,
        "type": kind,
        "name": name,
        "status": status,
        "cpu": 0.1,
        "mem": 512 * 1024 * 1024,
        "maxmem": 1024 * 1024 * 1024,
        "uptime": 120,
    }


@pytest.fixture
def mock_proxmox():
    return MagicMock(name="ProxmoxAPI")


@pytest.fixture
def executor(mock_proxmox):
    return ActionExecutor(proxmox=mock_proxmox)


def test_resolve_vm_finds_node_and_type(executor, mock_proxmox):
    mock_proxmox.cluster.resources.get.return_value = [
        _resource_entry(300, node="pve", kind="qemu", name="safe-test"),
        _resource_entry(110, node="pve", kind="lxc", name="ai-agents"),
    ]
    node, kind = executor.resolve_vm(300)
    assert node == "pve"
    assert kind == "qemu"
    mock_proxmox.cluster.resources.get.assert_called_with(type="vm")

    node2, kind2 = executor.resolve_vm(110)
    assert node2 == "pve"
    assert kind2 == "lxc"


def test_resolve_vm_missing_raises_without_post(executor, mock_proxmox):
    mock_proxmox.cluster.resources.get.return_value = [
        _resource_entry(100, name="other"),
    ]
    with pytest.raises(LookupError, match="300"):
        executor.resolve_vm(300)

    # No status.* posts attempted
    mock_proxmox.nodes.assert_not_called()


def test_execute_write_vm_start_qemu(executor, mock_proxmox):
    mock_proxmox.cluster.resources.get.return_value = [
        _resource_entry(300, kind="qemu"),
    ]
    status = mock_proxmox.nodes.return_value.qemu.return_value.status
    status.start.post.return_value = "UPID:pve:0000:start"

    result = executor.execute_write("vm_start", {"vmid": 300})
    assert "start" in result.lower() or "UPID" in result or "300" in result
    mock_proxmox.nodes.assert_called_with("pve")
    mock_proxmox.nodes.return_value.qemu.assert_called_with(300)
    status.start.post.assert_called_once_with()
    status.stop.post.assert_not_called()
    status.reboot.post.assert_not_called()


def test_execute_write_vm_stop_lxc(executor, mock_proxmox):
    mock_proxmox.cluster.resources.get.return_value = [
        _resource_entry(110, kind="lxc"),
    ]
    status = mock_proxmox.nodes.return_value.lxc.return_value.status
    status.stop.post.return_value = "UPID:pve:0000:stop"

    result = executor.execute_write("vm_stop", {"vmid": 110})
    assert "110" in result or "stop" in result.lower() or "UPID" in result
    mock_proxmox.nodes.return_value.lxc.assert_called_with(110)
    status.stop.post.assert_called_once_with()
    status.start.post.assert_not_called()


def test_execute_write_vm_reboot_qemu(executor, mock_proxmox):
    mock_proxmox.cluster.resources.get.return_value = [
        _resource_entry(300, kind="qemu"),
    ]
    status = mock_proxmox.nodes.return_value.qemu.return_value.status
    status.reboot.post.return_value = "UPID:pve:0000:reboot"

    result = executor.execute_write("vm_reboot", {"vmid": 300})
    assert result
    status.reboot.post.assert_called_once_with()


def test_execute_write_unknown_and_crisis_probe_raise(executor, mock_proxmox):
    mock_proxmox.cluster.resources.get.return_value = [
        _resource_entry(300),
    ]
    with pytest.raises(UnsupportedActionError):
        executor.execute_write("crisis_probe", {})
    with pytest.raises(UnsupportedActionError):
        executor.execute_write("rm_rf_root", {"vmid": 300})
    mock_proxmox.nodes.assert_not_called()


def test_execute_write_missing_vmid_raises(executor, mock_proxmox):
    with pytest.raises((KeyError, ValueError, TypeError)):
        executor.execute_write("vm_start", {})
    mock_proxmox.nodes.assert_not_called()


def test_execute_read_list_vms_no_write_posts(executor, mock_proxmox):
    mock_proxmox.cluster.resources.get.return_value = [
        _resource_entry(100, name="opnsense", status="running"),
        _resource_entry(300, name="safe-test", kind="qemu", status="stopped"),
        _resource_entry(110, name="ai-agents", kind="lxc", status="running"),
    ]
    text = executor.execute_read("list_vms", {})
    assert "100" in text
    assert "300" in text
    assert "110" in text
    assert "opnsense" in text or "safe-test" in text
    # No mutation posts
    mock_proxmox.nodes.assert_not_called()


def test_execute_read_vm_status_no_write_posts(executor, mock_proxmox):
    mock_proxmox.cluster.resources.get.return_value = [
        _resource_entry(300, name="safe-test", status="running"),
    ]
    current = mock_proxmox.nodes.return_value.qemu.return_value.status.current
    current.get.return_value = {
        "status": "running",
        "cpu": 0.05,
        "mem": 256 * 1024 * 1024,
        "maxmem": 1024 * 1024 * 1024,
        "uptime": 600,
    }

    text = executor.execute_read("vm_status", {"vmid": 300})
    assert "300" in text
    assert "running" in text.lower()
    mock_proxmox.nodes.return_value.qemu.return_value.status.start.post.assert_not_called()
    mock_proxmox.nodes.return_value.qemu.return_value.status.stop.post.assert_not_called()
    mock_proxmox.nodes.return_value.qemu.return_value.status.reboot.post.assert_not_called()


def test_execute_read_unknown_raises(executor, mock_proxmox):
    with pytest.raises(UnsupportedActionError):
        executor.execute_read("not_a_read", {})
    mock_proxmox.cluster.resources.get.assert_not_called()


def test_execute_read_vm_status_missing_vm(executor, mock_proxmox):
    mock_proxmox.cluster.resources.get.return_value = []
    with pytest.raises(LookupError):
        executor.execute_read("vm_status", {"vmid": 999})
    mock_proxmox.nodes.assert_not_called()


def test_phase4_action_membership():
    """Phase 4 seed actions are registered on READ/WRITE frozensets."""
    assert "snapshot_list" in READ_ACTIONS
    assert "service_uptime" in READ_ACTIONS
    assert "service_uptime_all" in READ_ACTIONS
    assert "snapshot_create" in WRITE_ACTIONS
    # crisis_probe remains catalog-only (no executor mapping)
    assert "crisis_probe" not in READ_ACTIONS
    assert "crisis_probe" not in WRITE_ACTIONS
