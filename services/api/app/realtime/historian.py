"""历史库写入：消费 Redis Stream，批量 COPY 写入 TimescaleDB。

使用消费组保证"至少一次"：写库成功后才 XACK；进程重启时先处理本消费者未确认的消息（PEL），
再读取新消息。多副本部署时各副本使用不同 consumer 名即可水平扩展。
"""

import asyncio
import json
import logging
from datetime import UTC, datetime

import asyncpg
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from . import keys

log = logging.getLogger(__name__)

GROUP = "historian"
BATCH = 500
WRITE_CATS = {"YC", "DD"}  # 遥信历史单独存事件表


def _ts(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, UTC)


class Historian:
    def __init__(self, redis: Redis, pool: asyncpg.Pool, analog_points: set[str], consumer: str = "api-1"):
        self.redis, self.pool, self.consumer = redis, pool, consumer
        self.analog_points = analog_points
        self.stats = {"rows": 0, "events": 0, "batches": 0, "errors": 0}

    async def run(self) -> None:
        await asyncio.gather(
            self._consume(keys.RT_CHANGES, self._write_analog),
            self._consume(keys.YX_EVENTS, self._write_events),
        )

    async def _ensure_group(self, stream: str) -> None:
        try:
            await self.redis.xgroup_create(stream, GROUP, id="0", mkstream=True)
        except ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    async def _consume(self, stream: str, writer) -> None:
        backoff = 1.0
        while True:
            try:
                await self._ensure_group(stream)
                last_id = "0"  # 先处理未确认消息
                while True:
                    resp = await self.redis.xreadgroup(GROUP, self.consumer, {stream: last_id}, count=BATCH,
                                                       block=1000)  # fmt: skip
                    entries = resp[0][1] if resp else []
                    if not entries:
                        if last_id == "0":
                            last_id = ">"
                        continue
                    await writer(entries)
                    await self.redis.xack(stream, GROUP, *[eid for eid, _ in entries])
                    self.stats["batches"] += 1
                    backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - 依赖不可用时退避重试，不丢消息（未 ACK）
                self.stats["errors"] += 1
                log.warning("historian %s error: %s; retry in %.0fs", stream, e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _write_analog(self, entries) -> None:
        rows = []
        for _, fields in entries:
            for pid, v, q, t in json.loads(fields["d"]):
                if pid in self.analog_points:
                    rows.append((_ts(t), pid, float(v), int(q)))
        if rows:
            async with self.pool.acquire() as conn:
                await conn.copy_records_to_table("analog_hist", records=rows,
                                                 columns=["ts", "point_id", "value", "quality"])  # fmt: skip
            self.stats["rows"] += len(rows)

    async def _write_events(self, entries) -> None:
        rows = []
        for _, fields in entries:
            e = json.loads(fields["e"])
            rows.append((_ts(e["dev_t"]) if e["dev_t"] else None, _ts(e["recv_t"]), e["p"], e["v"], e["old"],
                         e["cot"]))  # fmt: skip
        async with self.pool.acquire() as conn:
            await conn.copy_records_to_table("yx_event", records=rows,
                                             columns=["dev_ts", "recv_ts", "point_id", "value", "old_value", "cot"])  # fmt: skip
        self.stats["events"] += len(rows)
