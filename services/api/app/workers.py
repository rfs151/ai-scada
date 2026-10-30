"""后台任务监管：数据库迁移/模型同步（失败退避重试）→ 历史库写入；推送中心独立运行。"""

import asyncio
import logging

import asyncpg
from redis.asyncio import Redis

from app import db
from app.realtime.historian import Historian
from app.realtime.hub import Hub
from scadalib.model import Cat, Station

log = logging.getLogger(__name__)


class Workers:
    def __init__(self, pool: asyncpg.Pool, redis: Redis, hub: Hub, station: Station):
        self.pool, self.redis, self.hub, self.station = pool, redis, hub, station
        self.historian = Historian(redis, pool, {p.id for p in station.points if p.cat in (Cat.YC, Cat.DD)})
        self.db_ready = False
        self.tasks: list[asyncio.Task] = []

    def start(self) -> None:
        self.tasks = [
            asyncio.create_task(self.hub.run(), name="hub"),
            asyncio.create_task(self._db_then_historian(), name="historian"),
        ]

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def _db_then_historian(self) -> None:
        backoff = 1.0
        while True:
            try:
                applied = await db.migrate(self.pool)
                n = await db.sync_model(self.pool, self.station)
                log.info("database ready: %d migrations applied, %d points synced", len(applied), n)
                break
            except (OSError, asyncpg.PostgresError) as e:
                log.warning("database not ready (%s), retry in %.0fs", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
        self.db_ready = True
        await self.historian.run()

    def status(self) -> dict:
        return {
            "db_ready": self.db_ready,
            "historian": self.historian.stats,
            "hub": {"clients": len(self.hub.clients), **self.hub.stats},
            "tasks": {t.get_name(): ("running" if not t.done() else "stopped") for t in self.tasks},
        }
