"""依赖组件健康检查：每项检查独立超时，失败不抛异常，只返回状态。"""

import asyncio
import time
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

CHECK_TIMEOUT_S = 2.0


async def _timed(coro) -> dict[str, Any]:
    start = time.perf_counter()
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_S):
            detail = await coro
        status = "ok"
    except Exception as exc:  # noqa: BLE001 - 健康检查需要吞掉所有异常
        detail = {"error": f"{type(exc).__name__}: {exc}"[:200]}
        status = "down"
    return {"status": status, "latency_ms": round((time.perf_counter() - start) * 1000, 1), **detail}


async def _db(engine: AsyncEngine) -> dict[str, Any]:
    async with engine.connect() as conn:
        pg = (await conn.execute(text("SHOW server_version"))).scalar_one()
        ts = (
            await conn.execute(text("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'"))
        ).scalar_one_or_none()
    return {"postgres": pg, "timescaledb": ts}


async def _redis(client: Redis) -> dict[str, Any]:
    await client.ping()
    info = await client.info("server")
    return {"version": info.get("redis_version")}


async def check_all(engine: AsyncEngine, redis: Redis) -> dict[str, dict[str, Any]]:
    db, rds = await asyncio.gather(_timed(_db(engine)), _timed(_redis(redis)))
    return {"database": db, "redis": rds}
