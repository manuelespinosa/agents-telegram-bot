"""Unit tests for GateTools → ActionGate.propose only (no execute_write)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from pipeline.tools.proxmox_tools import SnapshotCreateTool, SnapshotCreateToolInput, VmStartTool
from pipeline.tools.uptime_tools import ServiceUptimeTool
from pipeline.tools import build_worker_tools


def _pending_result(request_id: str = "req-1", message: str = "awaiting approval"):
    return SimpleNamespace(
        status="pending",
        needs_approval=True,
        message=message,
        request_id=request_id,
        hitl_request_id=request_id,
        execution_result=None,
        result=None,
    )


def _completed_result(text: str = "ok"):
    return SimpleNamespace(
        status="completed",
        needs_approval=False,
        message="done",
        request_id=None,
        hitl_request_id=None,
        execution_result=text,
        result=text,
    )


def test_vm_start_tool_returns_hitl_pending_marker():
    gate = MagicMock()
    gate.propose.return_value = _pending_result("abc-123", "Approve vm_start 300")
    gate.executor = MagicMock()  # should never be used by tool
    pending: list = []
    tool = VmStartTool(gate=gate, actor="telegram:1", pending_collector=pending)

    out = tool._run(vmid=300, reason="boot test lab")

    assert out.startswith("HITL_PENDING:abc-123:")
    assert "Approve" in out
    gate.propose.assert_called_once_with(
        "vm_start",
        {"vmid": 300},
        reason="boot test lab",
        actor="telegram:1",
    )
    assert not hasattr(gate, "execute_write") or not gate.execute_write.called
    # tools must not call executor
    gate.executor.execute_write.assert_not_called()
    assert len(pending) == 1


def test_vm_start_tool_never_calls_execute_write_on_gate_or_executor():
    gate = MagicMock()
    gate.propose.return_value = _pending_result()
    gate.execute_write = MagicMock()
    tool = VmStartTool(gate=gate, actor="pipeline:worker")
    tool._run(vmid=300, reason="x")
    gate.propose.assert_called_once()
    gate.execute_write.assert_not_called()


def test_snapshot_create_args_schema_rejects_invalid_snapname():
    with pytest.raises(ValidationError):
        SnapshotCreateToolInput(vmid=300, snapname="bad name with spaces")
    with pytest.raises(ValidationError):
        SnapshotCreateToolInput(vmid=300, snapname="")
    ok = SnapshotCreateToolInput(vmid=300, snapname="pre-upgrade_1")
    assert ok.snapname == "pre-upgrade_1"


def test_snapshot_create_tool_proposes_correct_action():
    gate = MagicMock()
    gate.propose.return_value = _pending_result("s1")
    tool = SnapshotCreateTool(gate=gate, actor="pipeline:worker")
    out = tool._run(vmid=300, snapname="ai-test", description="unit", reason="snap")
    assert out.startswith("HITL_PENDING:")
    gate.propose.assert_called_once_with(
        "snapshot_create",
        {"vmid": 300, "snapname": "ai-test", "description": "unit"},
        reason="snap",
        actor="pipeline:worker",
    )


def test_service_uptime_tool_only_proposes_service_uptime():
    gate = MagicMock()
    gate.propose.return_value = _completed_result("UP jellyfin")
    tool = ServiceUptimeTool(gate=gate, actor="pipeline:worker")
    out = tool._run(service_name="jellyfin", reason="check")
    assert "UP jellyfin" in out
    gate.propose.assert_called_once_with(
        "service_uptime",
        {"service_name": "jellyfin"},
        reason="check",
        actor="pipeline:worker",
    )
    assert gate.propose.call_args[0][0] == "service_uptime"


def test_blocked_result_marker():
    gate = MagicMock()
    gate.propose.return_value = SimpleNamespace(
        status="blocked",
        needs_approval=False,
        message="budget paused",
        request_id=None,
        hitl_request_id=None,
        execution_result=None,
        result=None,
    )
    tool = VmStartTool(gate=gate, actor="x")
    assert tool._run(vmid=300, reason="y") == "BLOCKED:budget paused"


def test_build_worker_tools_includes_seed_actions_no_ssh():
    gate = MagicMock()
    tools = build_worker_tools(gate, actor="a")
    names = {t.name for t in tools}
    assert "list_vms" in names
    assert "vm_start" in names
    assert "snapshot_create" in names
    assert "service_uptime" in names
    assert "service_uptime_all" in names
    assert "ssh" not in names
    assert "shell" not in names
    assert "snapshot_rollback" not in names


def test_tools_modules_do_not_import_action_executor_for_writes():
    import pipeline.tools.base as base
    import pipeline.tools.proxmox_tools as px
    import pipeline.tools.uptime_tools as up
    import inspect

    for mod in (base, px, up):
        src = inspect.getsource(mod)
        # No import / call of ActionExecutor — only ActionGate.propose path
        assert "from action_executor" not in src
        assert "import action_executor" not in src
        assert "ActionExecutor" not in src
        assert "paramiko" not in src.lower()
        # No free-shell / remote shell tools
        assert "paramiko" not in src
        assert "subprocess" not in src
