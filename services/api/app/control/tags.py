"""挂牌：检修 / 禁止合闸 / 接地线。

内存缓存 + 数据库持久化；变化后经事件总线广播完整列表（数量很少，全量推送最简单可靠）。
  - 检修牌：禁止该设备一切遥控，并抑制该间隔告警（自动确认，单独标记）
  - 禁止合闸牌：禁止合闸，允许分闸
  - 接地线：作为额外接地点参与拓扑着色与五防（挂之前须验电：目标节点不带电）
"""

from __future__ import annotations

from datetime import datetime

import asyncpg
from redis.asyncio import Redis

from app.control.interlock import TagInfo
from app.realtime.bus import publish

KIND_CN = {"maint": "检修", "noclose": "禁止合闸", "ground": "接地线"}


def _row(r) -> dict:
    return {
        "id": r["id"],
        "device": r["device"],
        "kind": r["kind"],
        "kind_cn": KIND_CN[r["kind"]],
        "node": r["node"],
        "note": r["note"],
        "created_by": r["created_by"],
        "created_at": int(r["created_at"].timestamp() * 1000)
        if isinstance(r["created_at"], datetime)
        else None,
    }


class TagStore:
    def __init__(self, pool: asyncpg.Pool, redis: Redis):
        self.pool, self.redis = pool, redis
        self._tags: list[dict] | None = None

    async def all(self) -> list[dict]:
        if self._tags is None:
            rows = await self.pool.fetch("SELECT * FROM tag ORDER BY id")
            self._tags = [_row(r) for r in rows]
        return self._tags

    async def infos(self) -> list[TagInfo]:
        return [TagInfo(t["device"], t["kind"], t["node"]) for t in await self.all()]

    async def add(self, device: str, kind: str, node: str | None, note: str, by: str) -> dict:
        r = await self.pool.fetchrow(
            "INSERT INTO tag (device, kind, node, note, created_by) VALUES ($1,$2,$3,$4,$5) RETURNING *",
            device, kind, node, note, by,
        )  # fmt: skip
        await self._changed()
        return _row(r)

    async def remove(self, tag_id: int) -> dict | None:
        r = await self.pool.fetchrow("DELETE FROM tag WHERE id = $1 RETURNING *", tag_id)
        if r:
            await self._changed()
        return _row(r) if r else None

    async def clear(self) -> int:
        """摘除全部标识牌（自动复位用），以数据库为准，不依赖缓存。"""
        n = len(await self.pool.fetch("DELETE FROM tag RETURNING id"))
        await self._changed()
        return n

    async def _changed(self) -> None:
        self._tags = None
        await publish(self.redis, {"type": "tags", "tags": await self.all()})
