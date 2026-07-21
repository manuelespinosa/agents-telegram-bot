"""VMDiagnostics scaffolds for MON-04 (full diagnose in 02-02)."""
import pytest

from vm_diagnostics import VMDiagnostics


@pytest.mark.xfail(reason="MON Wave2: diagnose unknown VM + qemu/lxc until 02-02", strict=False)
@pytest.mark.asyncio
async def test_diagnose_unknown_vm():
    diag = VMDiagnostics()
    result = await diag.diagnose(99999)
    text = (result or "").lower()
    assert "no encontr" in text or "unknown" in text or "no existe" in text or "not found" in text


@pytest.mark.xfail(reason="MON Wave2: qemu vs lxc type detection until 02-02", strict=False)
@pytest.mark.asyncio
async def test_types(sample_vm_resource):
    diag = VMDiagnostics()
    # When implemented, diagnose should distinguish qemu vs lxc from resource type
    result = await diag.diagnose(sample_vm_resource["vmid"])
    text = (result or "").lower()
    assert "qemu" in text or "lxc" in text or "tipo" in text or "type" in text
