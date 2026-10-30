"""告警引擎：消费变化流与变位流，按规则产生告警，写库并经事件总线推送。

- 独立消费组 alarm：与历史库写入互不影响，各自记录消费位置
- 启动时用实时库断面初始化越限状态，不对"已经越限"的量重复告警
- 遥控变位判断：遥控服务执行前写入 ctl:recent:<设备>（20s 过期），变位到达时查询
- 检修挂牌间隔的告警标记为"检修抑制"并自动确认，不打扰值班员；告知类告警自动确认
"""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime

import asyncpg
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from app.alarm.datacheck import DataCheck
from app.alarm.rules import LEVEL_CN, AlarmDraft, AlarmRules
from app.control.tags import TagStore
from app.realtime import keys
from app.realtime.bus import publish
from app.realtime.hub import read_channels
from scadalib.model import Station

log = logging.getLogger(__name__)

GROUP = "alarm"
CHANNEL_POLL_S = 2.0
PDR_MERGE_MS = 20_000  # 同一次事故的后续动作（重合、后加速、备自投）在数秒内完成，并入同一条追忆
PDR_TRIGGERS = {"事故跳闸", "保护动作", "自动装置"}


def alarm_dict(r) -> dict:
    return {
        "id": r["id"],
        "ts": int(r["ts"].timestamp() * 1000),
        "level": r["level"],
        "level_cn": LEVEL_CN[r["level"]],
        "kind": r["kind"],
        "text": r["text"],
        "point": r["point_id"],
        "bay": r["bay"],
        "device": r["device"],
        "value": r["value"],
        "suppressed": r["suppressed"],
        "ack_by": r["ack_by"],
        "ack_ts": int(r["ack_ts"].timestamp() * 1000) if r["ack_ts"] else None,
    }


class AlarmEngine:
    def __init__(
        self, st: Station, redis: Redis, pool: asyncpg.Pool, tags: TagStore, consumer: str = "api-1"
    ):
        self.st, self.redis, self.pool, self.tags, self.consumer = st, redis, pool, tags, consumer
        self.rules = AlarmRules(st)
        self.datacheck = DataCheck(st)
        self.stats = {"alarms": 0, "errors": 0, "pdr": 0}
        self._last_pdr_ts: int | None = None

    async def run(self) -> None:
        await self._init()
        await asyncio.gather(self._consume(), self._channel_loop())

    async def _init(self) -> None:
        self.rules.init_values(await self._snapshot())
        for ch, info in (await read_channels(self.redis)).items():
            self.rules.channels[ch] = str(info.get("state"))
        for s in (keys.RT_CHANGES, keys.YX_EVENTS):
            try:
                # 从"现在"开始：历史变化不补发告警（断面已用于初始化）
                await self.redis.xgroup_create(s, GROUP, id="$", mkstream=True)
            except ResponseError as e:
                if "BUSYGROUP" not in str(e):
                    raise
                await self.redis.xgroup_setid(s, GROUP, id="$")

    async def _consume(self) -> None:
        streams = {keys.RT_CHANGES: ">", keys.YX_EVENTS: ">"}
        while True:
            try:
                resp = await self.redis.xreadgroup(GROUP, self.consumer, streams, count=500, block=1000)
                for stream, entries in resp or []:
                    drafts: list[AlarmDraft] = []
                    for _, f in entries:
                        if stream == keys.RT_CHANGES:
                            for pid, v, q, t in json.loads(f["d"]):
                                drafts += self.rules.on_analog(pid, v, q, t)
                        else:
                            e = json.loads(f["e"])
                            dev = e["p"].removesuffix(".POS") if e["p"].endswith(".POS") else None
                            cmd = None
                            if dev:
                                c = await self.redis.get(keys.CTL_RECENT_PREFIX + dev)
                                cmd = json.loads(c) if c else None
                            drafts += self.rules.on_yx(e, cmd)
                    await self.emit(drafts)
                    await self.redis.xack(stream, GROUP, *[eid for eid, _ in entries])
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                self.stats["errors"] += 1
                log.exception("alarm engine error")
                await asyncio.sleep(2)

    async def _channel_loop(self) -> None:
        while True:
            await asyncio.sleep(CHANNEL_POLL_S)
            try:
                now = int(time.time() * 1000)
                await self.emit(self.rules.tick(now))
                await self.emit(self.datacheck.run(await self._snapshot(), now))
                for ch, info in (await read_channels(self.redis)).items():
                    await self.emit(
                        self.rules.on_channel(ch, str(info.get("state")), now, str(info.get("detail", "")))
                    )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("alarm channel poll error")

    async def _snapshot(self) -> dict[str, tuple[float, int]]:
        raw = await self.redis.hgetall(keys.RT_VALUES)
        return {pid: (float(v["v"]), int(v["q"])) for pid, s in raw.items() for v in [json.loads(s)]}

    async def emit(self, drafts: list[AlarmDraft]) -> None:
        if not drafts:
            return
        tags = await self.tags.all()
        maint_devices = {t["device"] for t in tags if t["kind"] == "maint"}
        maint_bays = {self._bay_of(d) for d in maint_devices} - {None}
        for d in drafts:
            suppressed = d.device in maint_devices or (d.bay is not None and d.bay in maint_bays)
            ack_by = "检修抑制" if suppressed else ("自动" if d.level == 5 else None)
            row = await self.pool.fetchrow(
                """INSERT INTO alarm (ts, level, kind, point_id, bay, device, text, value, suppressed, ack_by, ack_ts)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10, CASE WHEN $10::text IS NULL THEN NULL ELSE now() END)
                   RETURNING *""",
                datetime.fromtimestamp(d.ts / 1000, UTC), d.level, d.kind, d.point, d.bay, d.device, d.text,
                d.value, suppressed, ack_by,
            )  # fmt: skip
            self.stats["alarms"] += 1
            await publish(self.redis, {"type": "alarm", "alarm": alarm_dict(row)})
            if d.level == 1 and d.kind in PDR_TRIGGERS:
                await self._pdr(d)

    async def _pdr(self, d: AlarmDraft) -> None:
        """事故追忆触发：一次事故只生成一条，触发时刻取第一个事故信号。"""
        if self._last_pdr_ts is not None and 0 <= d.ts - self._last_pdr_ts < PDR_MERGE_MS:
            return
        self._last_pdr_ts = d.ts
        row = await self.pool.fetchrow(
            "INSERT INTO pdr (ts, title, bay) VALUES ($1, $2, $3) RETURNING id",
            datetime.fromtimestamp(d.ts / 1000, UTC), d.text, d.bay,
        )  # fmt: skip
        self.stats["pdr"] += 1
        await publish(self.redis, {"type": "pdr", "id": row["id"], "ts": d.ts, "title": d.text})

    def _bay_of(self, device: str) -> str | None:
        for b in self.st.bays:
            if any(e.id == device for e in b.equipment) or b.id == device:
                return b.id
        return None
