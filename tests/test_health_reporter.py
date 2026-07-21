"""HealthReporter scaffolds for MON-03 (full collect in 02-02)."""
import pytest

from health_reporter import HealthReporter


@pytest.mark.xfail(reason="MON Wave2: collect_all_health sections nodes/VMs/storage until 02-02", strict=False)
@pytest.mark.asyncio
async def test_report_has_nodes_vms_storage_sections(sample_node_payload, sample_vm_resource):
    reporter = HealthReporter()
    report = await reporter.collect_all_health()
    text = report or ""
    lower = text.lower()
    # Real implementation must surface these sections (emoji tables OK)
    assert "node" in lower or "nodo" in lower or "proxmox" in lower
    assert "vm" in lower or "máquina" in lower or "machine" in lower
    assert "storage" in lower or "almacen" in lower or "disk" in lower
