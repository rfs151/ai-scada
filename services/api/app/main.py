import json
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from functools import partial

from fastapi import FastAPI
from redis.asyncio import Redis

from app import audit, db
from app.auth.security import LoginThrottle
from app.auth.users import authenticate
from app.control.interlock import Interlock
from app.control.service import ControlService, FepTransport
from app.control.state import read_positions
from app.control.tags import TagStore
from app.core.config import get_settings
from app.realtime import bus, keys
from app.realtime.hub import Hub
from app.routers import alarms, auth, control, data, health, model, realtime, tags
from app.routers import audit as audit_router
from app.workers import Workers
from scadalib.model import default_config_path, load_station


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    state = app.state
    state.settings = settings
    state.started_at = datetime.now(UTC)
    state.station = load_station(default_config_path())
    # 连接池与 Redis 客户端都是惰性连接：依赖未就绪时服务仍能启动
    state.pool = await db.create_pool(settings.database_url)
    state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    state.hub = Hub(state.redis)

    # ---- 认证
    state.jwt_secret = settings.jwt_secret or secrets.token_urlsafe(32)
    if not settings.jwt_secret:
        logging.getLogger(__name__).warning("JWT_SECRET 未配置，使用随机密钥（重启后需重新登录）")
    state.login_throttle = LoginThrottle()

    # ---- 遥控、五防、挂牌
    st, redis, pool = state.station, state.redis, state.pool
    state.interlock = Interlock.from_file(st, default_config_path().parent / "interlock.yaml")
    state.tags = TagStore(pool, redis)

    async def mark_commanded(device: str, info: dict) -> None:
        await redis.set(keys.CTL_RECENT_PREFIX + device, json.dumps(info, ensure_ascii=False), ex=20)

    state.control = ControlService(
        st, state.interlock, FepTransport(redis),
        positions=partial(read_positions, redis, st),
        tags=state.tags.infos,
        authenticate=partial(authenticate, pool),
        audit=partial(audit.write, pool),
        publish=partial(bus.publish, redis),
        mark_commanded=mark_commanded,
        session_s=settings.control_session_s,
        cmd_timeout_s=settings.control_cmd_timeout_s,
    )  # fmt: skip

    state.workers = Workers(state, settings) if settings.enable_workers else None
    if state.workers:
        state.workers.start()
    yield
    if state.workers:
        await state.workers.stop()
    await state.redis.aclose()
    await state.pool.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="电力系统 SCADA 演示系统 API",
        lifespan=lifespan,
    )
    for r in (health.router, model.router, data.router, realtime.router, auth.router, control.router,
              tags.router, alarms.router, audit_router.router):  # fmt: skip
        app.include_router(r)
    return app


app = create_app()
