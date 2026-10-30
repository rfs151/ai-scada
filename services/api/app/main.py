import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.routers import health, realtime


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # 引擎与 Redis 客户端都是惰性连接：依赖未就绪时服务仍能启动，健康检查会如实报告
    app.state.settings = settings
    app.state.started_at = datetime.now(UTC)
    app.state.engine = create_async_engine(settings.database_url, pool_size=5, pool_pre_ping=True)
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    yield
    await app.state.redis.aclose()
    await app.state.engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="电力系统 SCADA 演示系统 API",
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.include_router(realtime.router)
    return app


app = create_app()
