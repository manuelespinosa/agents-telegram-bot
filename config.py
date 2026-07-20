"""Configuración centralizada vía variables de entorno."""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    telegram_token: str = ""
    litellm_url: str = "http://litellm:4000/v1"
    proxmox_host: str = "192.168.1.91"
    proxmox_user: str = "api-ai-assistant@pve"
    proxmox_token_name: str = "api-ai-token"
    proxmox_token_value: str = ""
    proxmox_verify_ssl: bool = False
    report_hour: int = 8
    report_minute: int = 0
    allowed_users: list[str] = []

    model_config = {"env_prefix": ""}


settings = Settings()  # Singleton
