"""后台任务监管：数据库迁移/模型同步（失败退避重试）→ 演示账号 → 历史库写入 + 告警引擎；
推送中心、遥控超时清理独立运行。"""

import asyncio
import logging

import asyncpg

from app import db
from app.alarm.engine import AlarmEngine
from app.auth.users import seed_demo_users
from app.realtime.historian import Historian
from scadalib.model import Cat

log = logging.getLogger(__name__)


class Workers:
    def __init__(self, state, settings):
        self.state, self.settings = state, settings
        self.pool, self.redis, self.hub, self.station = state.pool, state.redis, state.hub, state.station
        self.historian = Historian(self.redis, self.pool,
                                   {p.id for p in self.station.points if p.cat in (Cat.YC, Cat.DD)})  # fmt: skip
        self.alarm = AlarmEngine(self.station, self.redis, self.pool, state.tags)
        self.db_ready = False
        self.tasks: list[asyncio.Task] = []

    def start(self) -> None:
        self.tasks = [
            asyncio.create_task(self.hub.run(), name="hub"),
            asyncio.create_task(self._db_then_consumers(), name="historian"),
            asyncio.create_task(self.state.control.run_sweeper(), name="control"),
        ]

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def _db_then_consumers(self) -> None:
        backoff = 1.0
        while True:
            try:
                applied = await db.migrate(self.pool)
                n = await db.sync_model(self.pool, self.station)
                if self.settings.demo_mode:
                    await seed_demo_users(self.pool, self.settings.demo_password)
                log.info("database ready: %d migrations applied, %d points synced", len(applied), n)
                break
            except (OSError, asyncpg.PostgresError) as e:
                log.warning("database not ready (%s), retry in %.0fs", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
        self.db_ready = True
        alarm = asyncio.create_task(self.alarm.run(), name="alarm")
        self.tasks.append(alarm)
        await self.historian.run()

    def status(self) -> dict:
        return {
            "db_ready": self.db_ready,
            "historian": self.historian.stats,
            "alarm": self.alarm.stats,
            "hub": {"clients": len(self.hub.clients), **self.hub.stats},
            "tasks": {t.get_name(): ("running" if not t.done() else "stopped") for t in self.tasks},
        }
