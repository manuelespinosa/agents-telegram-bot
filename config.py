"""Configuración centralizada vía variables de entorno."""
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_PACKAGE_DIR = Path(__file__).resolve().parent
_DEFAULT_UPTIME_PATH = str(_PACKAGE_DIR / "uptime_services.yaml")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    telegram_bot_token: str = Field(default="", validation_alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", validation_alias="TELEGRAM_CHAT_ID")
    telegram_allowed_users: str = Field(default="", validation_alias="TELEGRAM_ALLOWED_USERS")
    litellm_url: str = Field(default="http://litellm:4000/v1", validation_alias="LITELLM_URL")
    # LiteLLM OpenAI-compatible key for crewAI LLM clients (never log)
    litellm_api_key: str = Field(default="sk-proxy-key", validation_alias="LITELLM_API_KEY")
    proxmox_host: str = Field(default="192.168.1.91", validation_alias="PROXMOX_HOST")
    proxmox_user: str = Field(default="api-ai-assistant@pve", validation_alias="PROXMOX_USER")
    proxmox_token_name: str = Field(default="api-ai-token", validation_alias="PROXMOX_TOKEN_NAME")
    proxmox_token_value: str = Field(default="", validation_alias="PROXMOX_TOKEN_VALUE")
    proxmox_verify_ssl: bool = Field(default=False, validation_alias="PROXMOX_VERIFY_SSL")
    report_hour: int = 8
    report_minute: int = 0
    report_tz: str = Field(default="Europe/Madrid", validation_alias="REPORT_TZ")

    # HITL / budget (Phase 3) — never log secret values
    hitl_hmac_secret: str = Field(default="", validation_alias="HITL_HMAC_SECRET")
    budget_max_usd: float = Field(default=0.50, validation_alias="BUDGET_MAX_USD")
    hitl_timeout_sec: int = Field(default=300, validation_alias="HITL_TIMEOUT_SEC")
    cost_db_path: str = Field(
        default="/app/data/cost_logs.db", validation_alias="COST_DB_PATH"
    )
    hitl_db_path: str = Field(
        default="/app/data/hitl.sqlite", validation_alias="HITL_DB_PATH"
    )

    # Phase 4 pipeline / uptime (D-04/D-07) — secrets never logged
    uptime_services_path: str = Field(
        default=_DEFAULT_UPTIME_PATH, validation_alias="UPTIME_SERVICES_PATH"
    )
    clarification_ttl_sec: int = Field(default=600, validation_alias="CLARIFICATION_TTL_SEC")
    pipeline_router_model: str = Field(
        default="gemini-flash", validation_alias="PIPELINE_ROUTER_MODEL"
    )
    pipeline_worker_model: str = Field(
        default="qwen-coder", validation_alias="PIPELINE_WORKER_MODEL"
    )
    pipeline_crisis_model: str = Field(
        default="deepseek-r1", validation_alias="PIPELINE_CRISIS_MODEL"
    )

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
