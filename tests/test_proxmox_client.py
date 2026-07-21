"""Settings exposes token fields for ProxmoxAPI kwargs."""
from config import Settings


def test_settings_exposes_proxmox_token_fields():
    s = Settings(
        PROXMOX_HOST="192.168.1.91",
        PROXMOX_USER="api-ai-assistant@pve",
        PROXMOX_TOKEN_NAME="api-ai-token",
        PROXMOX_TOKEN_VALUE="secret-value",
        PROXMOX_VERIFY_SSL="false",
    )
    # Fields required by ProxmoxAPI(...) construction in 02-02
    assert s.proxmox_host == "192.168.1.91"
    assert s.proxmox_user == "api-ai-assistant@pve"
    assert s.proxmox_token_name == "api-ai-token"
    assert s.proxmox_token_value == "secret-value"
    assert s.proxmox_verify_ssl is False

    kwargs = {
        "host": s.proxmox_host,
        "user": s.proxmox_user,
        "token_name": s.proxmox_token_name,
        "token_value": s.proxmox_token_value,
        "verify_ssl": s.proxmox_verify_ssl,
    }
    assert kwargs["token_value"] == "secret-value"
    assert set(kwargs) >= {"host", "user", "token_name", "token_value", "verify_ssl"}
