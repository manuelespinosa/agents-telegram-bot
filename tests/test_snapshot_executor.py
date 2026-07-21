"""Snapshot executor unit tests — MagicMock proxmox, no live cluster (D-05/D-06)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from action_executor import READ_ACTIONS, WRITE_ACTIONS, ActionExecutor, UnsupportedActionError


def _resource_entry(
    vmid: int,
    *,
    node: str = "pve",
    kind: str = "qemu",
    name: str = "safe-test",
    status: str = "running",
) -> dict:
    return {
        "vmid": vmid,
        "node": node,
        "type": kind,
        "name": name,
        "status": status,
    }


@pytest.fixture
def mock_proxmox():
    return MagicMock(name="ProxmoxAPI")


@pytest.fixture
def executor(mock_proxmox):
    return ActionExecutor(proxmox=mock_proxmox)


def test_snapshot_list_in_read_actions():
    assert "snapshot_list" in READ_ACTIONS
    assert "snapshot_create" in WRITE_ACTIONS


def test_execute_read_snapshot_list_skips_current(executor, mock_proxmox):
    mock_proxmox.cluster.resources.get.return_value = [
        _resource_entry(300, kind="qemu"),
    ]
    guest = mock_proxmox.nodes.return_value.qemu.return_value
    guest.snapshot.get.return_value = [
        {"name": "current", "description": "synthetic"},
        {"name": "pre-upgrade", "description": "before pkg"},
        {"name": "ai-test", "description": ""},
    ]

    text = executor.execute_read("snapshot_list", {"vmid": 300})
    assert "pre-upgrade" in text
    assert "ai-test" in text
    assert "current" not in text.split("\n")[2:] or "current" not in [
        line for line in text.splitlines() if line.startswith("•")
    ]
    # Explicit: bullet lines must not include the synthetic current snap
    bullets = [ln for ln in text.splitlines() if ln.startswith("•")]
    assert all("current" not in ln for ln in bullets)
    guest.snapshot.get.assert_called_once_with()
    guest.snapshot.post.assert_not_called()


def test_execute_read_snapshot_list_empty(executor, mock_proxmox):
    mock_proxmox.cluster.resources.get.return_value = [
        _resource_entry(300, kind="lxc"),
    ]
    guest = mock_proxmox.nodes.return_value.lxc.return_value
    guest.snapshot.get.return_value = [{"name": "current"}]

    text = executor.execute_read("snapshot_list", {"vmid": 300})
    assert "sin snapshots" in text.lower() or "(sin snapshots)" in text


def test_execute_write_snapshot_create_qemu(executor, mock_proxmox):
    mock_proxmox.cluster.resources.get.return_value = [
        _resource_entry(300, kind="qemu"),
    ]
    guest = mock_proxmox.nodes.return_value.qemu.return_value
    guest.snapshot.post.return_value = "UPID:pve:0000:snap"

    result = executor.execute_write(
        "snapshot_create",
        {"vmid": 300, "snapname": "ai-ts", "description": "t"},
    )
    assert "ai-ts" in result
    assert "UPID" in result
    guest.snapshot.post.assert_called_once_with(snapname="ai-ts", description="t")
    guest.status.start.post.assert_not_called()


def test_execute_write_snapshot_create_lxc(executor, mock_proxmox):
    mock_proxmox.cluster.resources.get.return_value = [
        _resource_entry(110, kind="lxc", name="ai-agents"),
    ]
    guest = mock_proxmox.nodes.return_value.lxc.return_value
    guest.snapshot.post.return_value = "UPID:pve:0000:lxc-snap"

    result = executor.execute_write(
        "snapshot_create",
        {"vmid": 110, "snapname": "pre-change"},
    )
    assert "pre-change" in result
    guest.snapshot.post.assert_called_once_with(snapname="pre-change", description="")


def test_execute_write_crisis_probe_still_unsupported(executor, mock_proxmox):
    with pytest.raises(UnsupportedActionError):
        executor.execute_write("crisis_probe", {})
    mock_proxmox.nodes.assert_not_called()


def test_no_paramiko_or_ssh_imports_in_executor_module():
    import action_executor as mod
    import inspect

    src = inspect.getsource(mod)
    assert "paramiko" not in src
    assert "import ssh" not in src
    assert "ansible" not in src.lower()
