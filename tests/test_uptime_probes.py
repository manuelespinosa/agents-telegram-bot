"""Curated service uptime probe unit tests (D-07) — no ad-hoc hosts."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from action_executor import (
    ActionExecutor,
    parse_uptime_services_yaml,
)


CURATED = [
    {
        "name": "jellyfin",
        "probe": "http",
        "url": "http://jellyfin.local:8096/health",
        "enabled": True,
    },
    {
        "name": "immich",
        "probe": "http",
        "url": "http://immich.local:2283/api/server-info/ping",
        "enabled": False,
    },
    {
        "name": "mqtt",
        "probe": "tcp",
        "host": "mqtt.local",
        "port": 1883,
        "enabled": True,
    },
]


@pytest.fixture
def executor():
    return ActionExecutor(proxmox=MagicMock(), uptime_services=CURATED)


def test_parse_uptime_seed_yaml_shape():
    text = """
- name: jellyfin
  probe: http
  url: http://example/health
  enabled: false
- name: mqtt
  probe: tcp
  host: mqtt.local
  port: 1883
  enabled: true
"""
    services = parse_uptime_services_yaml(text)
    assert len(services) == 2
    assert services[0]["name"] == "jellyfin"
    assert services[0]["enabled"] is False
    assert services[1]["name"] == "mqtt"
    assert services[1]["port"] == 1883
    assert services[1]["enabled"] is True


def test_unknown_service_blocked_without_sockets(executor):
    with (
        patch.object(ActionExecutor, "_probe_http") as http_mock,
        patch.object(ActionExecutor, "_probe_tcp") as tcp_mock,
        patch("action_executor.socket.create_connection") as sock_mock,
        patch("action_executor.httpx.Client") as client_mock,
    ):
        text = executor.execute_read(
            "service_uptime", {"service_name": "unknown_xyz"}
        )
        assert "unknown" in text.lower() or "not in curated" in text.lower()
        assert "unknown_xyz" in text
        http_mock.assert_not_called()
        tcp_mock.assert_not_called()
        sock_mock.assert_not_called()
        client_mock.assert_not_called()


def test_service_uptime_jellyfin_http_up(executor):
    with patch.object(ActionExecutor, "_probe_http", return_value=True) as http_mock:
        text = executor.execute_read(
            "service_uptime", {"service_name": "jellyfin"}
        )
        assert "jellyfin" in text.lower()
        assert "UP" in text
        http_mock.assert_called_once_with("http://jellyfin.local:8096/health")


def test_service_uptime_jellyfin_http_down(executor):
    with patch.object(ActionExecutor, "_probe_http", return_value=False):
        text = executor.execute_read(
            "service_uptime", {"service_name": "jellyfin"}
        )
        assert "DOWN" in text


def test_service_uptime_all_only_enabled(executor):
    with (
        patch.object(ActionExecutor, "_probe_http", return_value=True) as http_mock,
        patch.object(ActionExecutor, "_probe_tcp", return_value=True) as tcp_mock,
    ):
        text = executor.execute_read("service_uptime_all", {})
        assert "jellyfin" in text
        assert "mqtt" in text
        # immich is enabled: false — must not be probed
        assert "immich" not in text
        http_mock.assert_called_once()
        tcp_mock.assert_called_once_with("mqtt.local", 1883)


def test_service_uptime_all_none_enabled():
    services = [
        {
            "name": "grafana",
            "probe": "http",
            "url": "http://grafana.local:3000/api/health",
            "enabled": False,
        }
    ]
    ex = ActionExecutor(proxmox=MagicMock(), uptime_services=services)
    with patch.object(ActionExecutor, "_probe_http") as http_mock:
        text = ex.execute_read("service_uptime_all", {})
        assert "no enabled" in text.lower() or "enabled" in text.lower()
        http_mock.assert_not_called()


def test_httpx_client_used_for_http_probe(executor):
    """Integration-style mock of httpx.Client for jellyfin UP path."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.return_value = mock_resp

    with patch("action_executor.httpx.Client", return_value=mock_client) as client_cls:
        text = executor.execute_read(
            "service_uptime", {"service_name": "jellyfin"}
        )
        assert "UP" in text
        client_cls.assert_called_once()
        kwargs = client_cls.call_args.kwargs
        assert kwargs.get("timeout") == 3.0
        assert kwargs.get("follow_redirects") is True
        assert kwargs.get("verify") is False
