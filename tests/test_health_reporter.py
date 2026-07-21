"""HealthReporter tests for MON-03 (mocked Proxmox + Docker)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from health_reporter import HealthReporter
from report_format import split_message


class _FakeProxmox:
    def __init__(self, node_payload, vm_resource):
        self._node = dict(node_payload)
        self._vm = dict(vm_resource)
        self.nodes = MagicMock()
        self.nodes.get.return_value = [self._node]
        # storage per node
        storage_api = MagicMock()
        storage_api.get.return_value = [
            {
                "storage": "local-lvm",
                "used": 50_000_000_000,
                "total": 100_000_000_000,
                "active": 1,
            }
        ]
        self.nodes.return_value.storage = storage_api

        self.cluster = MagicMock()
        self.cluster.resources.get.return_value = [self._vm]


class _FakeCrawler:
    async def get_critical_events(self, container="", lines=100):
        return [
            {
                "timestamp": "t",
                "level": "ERROR",
                "message": "boom",
                "container": "litellm",
            }
        ]

    async def summarize(self, events):
        return "• Logs críticos: ERROR=1 (litellm)"


@pytest.mark.asyncio
async def test_report_has_nodes_vms_storage_sections(
    sample_node_payload, sample_vm_resource
):
    px = _FakeProxmox(sample_node_payload, sample_vm_resource)
    reporter = HealthReporter(proxmox=px, log_crawler=_FakeCrawler())
    report = await reporter.collect_all_health()
    text = report or ""
    lower = text.lower()
    assert "node" in lower or "nodo" in lower or "proxmox" in lower
    assert "vm" in lower or "máquina" in lower or "machine" in lower or "cts" in lower
    assert "storage" in lower or "almacen" in lower or "disk" in lower or "local-lvm" in lower
    assert "docker" in lower or "logs" in lower or "evento" in lower


@pytest.mark.asyncio
async def test_cpu_uses_fraction_times_100(sample_node_payload, sample_vm_resource):
    """cpu=0.25 → 25% (not cpu/maxcpu)."""
    node = dict(sample_node_payload)
    node["cpu"] = 0.25
    node["maxcpu"] = 8
    px = _FakeProxmox(node, sample_vm_resource)
    reporter = HealthReporter(proxmox=px, log_crawler=_FakeCrawler())
    report = await reporter.collect_all_health()
    assert "25%" in report
    # Must NOT show nonsense like 3% from 0.25/8*100
    assert "3%" not in report


@pytest.mark.asyncio
async def test_send_daily_report_uses_chat_id_and_store(
    sample_node_payload, sample_vm_resource, monkeypatch, tmp_path
):
    from chat_store import ChatStore
    from config import settings

    monkeypatch.setattr(settings, "telegram_chat_id", "111")
    store = ChatStore(str(tmp_path / "chats.sqlite"))
    store.add_chat(222)

    px = _FakeProxmox(sample_node_payload, sample_vm_resource)
    reporter = HealthReporter(proxmox=px, log_crawler=_FakeCrawler())

    sent = []

    class FakeBot:
        async def send_message(self, chat_id, text, **kwargs):
            sent.append({"chat_id": chat_id, "text": text, "kwargs": kwargs})

    app = SimpleNamespace(bot=FakeBot(), bot_data={"chat_store": store})
    await reporter.send_daily_report(app)

    chat_ids = {s["chat_id"] for s in sent}
    assert 111 in chat_ids
    assert 222 in chat_ids
    # No Markdown parse_mode on dynamic content
    for s in sent:
        assert s["kwargs"].get("parse_mode") in (None, )


def test_split_message_under_limit():
    big = "x" * 8000
    chunks = split_message(big, limit=3500)
    assert len(chunks) >= 2
    assert all(len(c) <= 3500 for c in chunks)
