from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "PowerGrid SCADA"
    app_version: str = "dev"
    log_level: str = "info"

    database_url: str = "postgresql+asyncpg://scada:scada@localhost:5432/scada"
    redis_url: str = "redis://localhost:6379/0"

    # WebSocket 心跳周期（秒）
    ws_heartbeat_s: float = 1.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
