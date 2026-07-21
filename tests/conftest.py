"""Shared fixtures for telegram-bot unit tests (Wave 0)."""
import sys
from pathlib import Path

import pytest

# Ensure telegram-bot package root is importable when running from tests/
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def sample_node_payload():
    """Proxmox node status-like payload (cpu is fraction 0–1)."""
    return {
        "node": "pve",
        "status": "online",
        "cpu": 0.23,
        "maxcpu": 8,
        "mem": 8_589_934_592,
        "maxmem": 34_359_738_368,
        "uptime": 86400,
    }


@pytest.fixture
def sample_vm_resource():
    """Proxmox cluster.resources type=vm entry."""
    return {
        "vmid": 100,
        "name": "opnsense",
        "type": "qemu",
        "node": "pve",
        "status": "running",
        "cpu": 0.05,
        "maxcpu": 2,
        "mem": 1_073_741_824,
        "maxmem": 4_294_967_296,
        "uptime": 3600,
    }


@pytest.fixture
def sample_snapshot():
    """Snapshot entry with snaptime in Unix seconds."""
    return {
        "name": "pre-upgrade",
        "snaptime": 1_700_000_000,
        "description": "before package upgrade",
        "vmstate": 0,
    }


@pytest.fixture
def sample_docker_log_lines():
    """Docker log lines including ERROR and OOM for LogCrawler tests."""
    return [
        "2024-01-15T08:00:01.000000000Z INFO starting worker",
        "2024-01-15T08:00:02.000000000Z ERROR connection refused to db:5432",
        "2024-01-15T08:00:03.000000000Z FATAL cannot recover state",
        "2024-01-15T08:00:04.000000000Z WARN Memory cgroup out of memory: Kill process 42 (python) score 900 or sacrifice child",
        "2024-01-15T08:00:05.000000000Z ERROR no space left on device",
        "2024-01-15T08:00:06.000000000Z INFO heartbeat ok",
    ]


@pytest.fixture
def allowed_settings(monkeypatch):
    """Settings instance with a known allowlist (no real env required)."""
    from config import Settings

    return Settings(
        TELEGRAM_BOT_TOKEN="test-token",
        TELEGRAM_ALLOWED_USERS="111,222",
        TELEGRAM_CHAT_ID="111",
    )


@pytest.fixture
def hitl_hmac_secret() -> bytes:
    """Fixed 32+ byte HMAC secret for deterministic unit tests."""
    return b"unit-test-hitl-hmac-secret-32b!!"


@pytest.fixture
def hitl_db_path(tmp_path):
    """Temporary SQLite path for HITLStore."""
    return str(tmp_path / "hitl.sqlite")


@pytest.fixture
def cost_db_path(tmp_path):
    """Temporary SQLite path for BudgetGate cost_logs."""
    return str(tmp_path / "cost_logs.db")
