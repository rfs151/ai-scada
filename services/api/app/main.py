import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from redis.asyncio import Redis

from app import db
from app.core.config import get_settings
from app.realtime.hub import Hub
from app.routers import data, health, model, realtime
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
    state.workers = (
        Workers(state.pool, state.redis, state.hub, state.station) if settings.enable_workers else None
    )
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
    for r in (health.router, model.router, data.router, realtime.router):
        app.include_router(r)
    return app


app = create_app()
