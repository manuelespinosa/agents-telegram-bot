"""VMDiagnostics tests for MON-04 (mocked qemu/lxc paths)."""
from unittest.mock import MagicMock

import pytest

from vm_diagnostics import VMDiagnostics, format_snaptime


def _proxmox_for(vm_resource, status=None, snaps=None, include_vm=True):
    px = MagicMock()
    resources = [dict(vm_resource)] if include_vm else []
    px.cluster.resources.get.return_value = resources

    node_name = vm_resource.get("node", "pve")
    vmid = vm_resource.get("vmid", 100)
    vtype = (vm_resource.get("type") or "qemu").lower()

    status = status or {
        "status": "running",
        "cpu": 0.05,
        "mem": 1_073_741_824,
        "maxmem": 4_294_967_296,
        "uptime": 3600,
        "agent": 1,
    }
    snaps = snaps if snaps is not None else [
        {"name": "current", "snaptime": 0},
        {
            "name": "pre-upgrade",
            "snaptime": 1_700_000_000,
            "description": "before package upgrade",
        },
    ]

    api = MagicMock()
    api.status.current.get.return_value = status
    api.snapshot.get.return_value = snaps

    node_api = MagicMock()
    if vtype == "lxc":
        node_api.lxc.return_value = api
    else:
        node_api.qemu.return_value = api

    # nodes(node) → node_api
    def nodes_call(n):
        assert n == node_name
        return node_api

    px.nodes.side_effect = nodes_call
    return px


@pytest.mark.asyncio
async def test_diagnose_unknown_vm():
    px = MagicMock()
    px.cluster.resources.get.return_value = []
    diag = VMDiagnostics(proxmox=px)
    result = await diag.diagnose(99999)
    text = (result or "").lower()
    assert (
        "no encontr" in text
        or "unknown" in text
        or "no existe" in text
        or "not found" in text
    )


@pytest.mark.asyncio
async def test_types_qemu(sample_vm_resource, sample_snapshot):
    px = _proxmox_for(
        sample_vm_resource,
        snaps=[
            {"name": "current"},
            sample_snapshot,
        ],
    )
    diag = VMDiagnostics(proxmox=px)
    result = await diag.diagnose(sample_vm_resource["vmid"])
    text = (result or "").lower()
    assert "qemu" in text or "tipo" in text or "type" in text
    assert "cpu" in text
    assert "5.0%" in result or "5%" in result  # 0.05 * 100
    assert "uptime" in text
    assert "agent" in text
    assert "pre-upgrade" in result
    # snaptime 1_700_000_000 as seconds → 2023-ish, not 1970
    assert "1970" not in result


@pytest.mark.asyncio
async def test_types_lxc():
    lxc = {
        "vmid": 110,
        "name": "ct110",
        "type": "lxc",
        "node": "pve",
        "status": "running",
        "cpu": 0.1,
        "maxcpu": 2,
        "mem": 512_000_000,
        "maxmem": 2_000_000_000,
        "uptime": 120,
    }
    px = _proxmox_for(lxc, status={
        "status": "running",
        "cpu": 0.1,
        "mem": 512_000_000,
        "maxmem": 2_000_000_000,
        "uptime": 120,
    })
    diag = VMDiagnostics(proxmox=px)
    result = await diag.diagnose(110)
    text = (result or "").lower()
    assert "lxc" in text
    assert "10.0%" in result or "10%" in result


def test_snaptime_seconds_not_divided_by_1e6():
    # 1700000000 seconds → year 2023
    s = format_snaptime(1_700_000_000)
    assert "2023" in s or "2024" in s
    assert "1970" not in s


def test_snaptime_ms_when_gt_1e12():
    # milliseconds epoch
    s = format_snaptime(1_700_000_000_000)
    assert "2023" in s or "2024" in s
