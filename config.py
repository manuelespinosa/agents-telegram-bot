"""Configuración centralizada vía variables de entorno."""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    telegram_bot_token: str = Field(default="", validation_alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", validation_alias="TELEGRAM_CHAT_ID")
    telegram_allowed_users: str = Field(default="", validation_alias="TELEGRAM_ALLOWED_USERS")
    litellm_url: str = Field(default="http://litellm:4000/v1", validation_alias="LITELLM_URL")
    proxmox_host: str = Field(default="192.168.1.91", validation_alias="PROXMOX_HOST")
    proxmox_user: str = Field(default="api-ai-assistant@pve", validation_alias="PROXMOX_USER")
    proxmox_token_name: str = Field(default="api-ai-token", validation_alias="PROXMOX_TOKEN_NAME")
    proxmox_token_value: str = Field(default="", validation_alias="PROXMOX_TOKEN_VALUE")
    proxmox_verify_ssl: bool = Field(default=False, validation_alias="PROXMOX_VERIFY_SSL")
    report_hour: int = 8
    report_minute: int = 0
    report_tz: str = Field(default="Europe/Madrid", validation_alias="REPORT_TZ")

    def allowed_user_ids(self) -> set[int]:
        """Parse TELEGRAM_ALLOWED_USERS CSV. Empty → empty set (deny-if-empty)."""
        if not self.telegram_allowed_users.strip():
            return set()
        return {
            int(x.strip())
            for x in self.telegram_allowed_users.split(",")
            if x.strip()
        }


settings = Settings()  # Singleton


def is_user_authorized(uid: int | None, allowed: set[int] | None = None) -> bool:
    """Pure allowlist check: empty allowlist denies all; missing uid denies."""
    if allowed is None:
        allowed = settings.allowed_user_ids()
    if not allowed or uid is None:
        return False
    return uid in allowed
