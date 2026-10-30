from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "PowerGrid SCADA"
    app_version: str = "dev"
    log_level: str = "info"

    database_url: str = "postgresql://scada:scada@localhost:5432/scada"
    redis_url: str = "redis://localhost:6379/0"

    # 后台任务（迁移、历史库写入、推送中心）；单元测试时关闭
    enable_workers: bool = True

    # WebSocket 心跳周期（秒）
    ws_heartbeat_s: float = 1.0

    # ---- 认证
    # JWT 签名密钥；留空则启动时随机生成（重启后需重新登录）
    jwt_secret: str = ""
    token_ttl_min: int = 480  # 一个值班班次
    # 演示模式：自动创建演示账号，并在登录页提示账号口令
    demo_mode: bool = True
    demo_password: str = "scada@2024"
    # 演示环境自动复位间隔（分钟），0 关闭；有人操作后推迟到空闲 10 分钟再复位
    auto_reset_min: float = 60
    sim_speed: float = 60  # 仿真倍速（与子站 SIM_SPEED 一致）
    brief_model: str = "claude-opus-5-5"  # 事故简报大模型（需 ANTHROPIC_API_KEY）

    # ---- 遥控
    control_session_s: float = 120.0  # 发起 → 执行的总时限
    control_cmd_timeout_s: float = 15.0  # 等待子站确认


@lru_cache
def get_settings() -> Settings:
    return Settings()
