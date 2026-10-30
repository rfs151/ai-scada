"""前置采集服务（FEP）：IEC 104 主站 → Redis 实时库。

Redis 数据结构（键名前缀统一，api 侧在 app/realtime/keys.py 对应）：
    rt:val           Hash    测点 → {"v","q","t"} 实时值
    rt:chg           Stream  变化数据，每条 d=[[点号, 值, 品质, 时标ms], ...]
    ev:yx            Stream  遥信变位事件（SOE）
    ch:<通道>         Hash    通道状态与统计
    fep:frames       Stream  原始报文（报文监视）
    cmd:req          Stream  控制请求（api → fep），消费组 fep
    cmd:res:<请求id>  List    控制结果（fep → api）
"""

import asyncio
import contextlib
import json
import logging
import os
import signal
import time
from datetime import UTC, datetime

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from scadalib.iec104 import Asdu, Cycles, Iec104Client
from scadalib.iec104.describe import describe
from scadalib.model import Station, default_config_path, load_station

from .processor import Batch, Processor, command_value, now_ms

log = logging.getLogger("fep")

STREAM_MAXLEN = 100_000
FRAMES_MAXLEN = 2_000
YX_MAXLEN = 20_000


class Fep:
    def __init__(self, st: Station, redis: Redis, host: str, port: int, channel: str = "rtu1"):
        self.st, self.redis, self.channel = st, redis, channel
        self.proc = Processor(st)
        self.ch_key = f"ch:{channel}"
        self.stats = {"rx_asdu": 0, "unknown_ioa": 0, "changes": 0}
        self._frames: list[tuple[str, bytes, int]] = []
        self.client = Iec104Client(
            host,
            port,
            ca=st.ca,
            on_data=self._on_data,
            on_state=self._on_state,
            on_frame=self._on_frame,
            cycles=Cycles(
                gi_s=float(os.environ.get("GI_PERIOD_S", "900")),
                clock_s=float(os.environ.get("CLOCK_SYNC_PERIOD_S", "600")),
                counters_s=float(os.environ.get("COUNTER_PERIOD_S", "300")),
            ),
        )

    async def run(self) -> None:
        await self.redis.hset(self.ch_key, mapping={"state": "down", "peer": f"{self.client.host}:{self.client.port}",
                                                    "since": now_ms()})  # fmt: skip
        await asyncio.gather(self.client.run_forever(), self._flush_loop(), self._command_loop())

    # ------------------------------------------------------------ 数据

    async def _on_data(self, a: Asdu) -> None:
        self.stats["rx_asdu"] += 1
        b = self.proc.process(a)
        self.stats["unknown_ioa"] += b.unknown
        await self._write(b)

    async def _write(self, b: Batch) -> None:
        if not b.values:
            return
        pipe = self.redis.pipeline(transaction=False)
        pipe.hset("rt:val", mapping={pid: json.dumps(v.to_dict()) for pid, v in b.values.items()})
        if b.changes:
            self.stats["changes"] += len(b.changes)
            d = [[pid, v.v, v.q, v.t] for pid, v in b.changes.items()]
            pipe.xadd("rt:chg", {"d": json.dumps(d)}, maxlen=STREAM_MAXLEN, approximate=True)
        for e in b.yx_events:
            pipe.xadd("ev:yx", {"e": json.dumps(e.to_dict())}, maxlen=YX_MAXLEN, approximate=True)
        await pipe.execute()

    async def _on_state(self, state: str, detail: str) -> None:
        log.info("channel %s: %s %s", self.channel, state, detail)
        fields = {"state": state, "detail": detail, "since": now_ms()}
        if state == "up":
            fields["connected_at"] = now_ms()
        await self.redis.hset(self.ch_key, mapping=fields)
        if state == "down":
            await self._write(self.proc.mark_not_topical())

    # ------------------------------------------------------------ 报文监视

    def _on_frame(self, direction: str, raw: bytes) -> None:
        self._frames.append((direction, raw, now_ms()))

    async def _flush_loop(self) -> None:
        """报文与统计每 200ms 批量写入，避免逐帧往返 Redis。"""
        while True:
            await asyncio.sleep(0.2)
            frames, self._frames = self._frames, []
            pipe = self.redis.pipeline(transaction=False)
            for direction, raw, t in frames:
                pipe.xadd("fep:frames", {"dir": direction, "hex": raw.hex(" "), "text": describe(raw), "t": t,
                                         "ch": self.channel}, maxlen=FRAMES_MAXLEN, approximate=True)  # fmt: skip
            link = self.client.link
            stats = dict(self.stats) | self.client.stats
            if link:
                stats |= link.stats | {"unacked": link.unacked, "vs": link.vs, "vr": link.vr}
            pipe.hset(self.ch_key, mapping={k: int(v) for k, v in stats.items()})
            try:
                await pipe.execute()
            except Exception:  # noqa: BLE001 - Redis 短暂不可用时不影响采集
                log.exception("flush failed")

    # ------------------------------------------------------------ 控制

    async def _command_loop(self) -> None:
        """消费 api 下发的控制请求：{"id","point","action": select|execute|cancel,"value"}。"""
        with contextlib.suppress(ResponseError):  # BUSYGROUP：消费组已存在
            await self.redis.xgroup_create("cmd:req", "fep", id="$", mkstream=True)
        while True:
            resp = await self.redis.xreadgroup("fep", "fep-1", {"cmd:req": ">"}, count=1, block=5000)
            for _, entries in resp or []:
                for entry_id, fields in entries:
                    req = json.loads(fields["r"])
                    res = await self._execute(req)
                    key = f"cmd:res:{req['id']}"
                    await self.redis.rpush(key, json.dumps(res))
                    await self.redis.expire(key, 60)
                    await self.redis.xack("cmd:req", "fep", entry_id)

    async def _execute(self, req: dict) -> dict:
        started = time.monotonic()
        try:
            p = self.st.point(req["point"])
        except KeyError:
            return {"ok": False, "stage": "fep", "detail": f"unknown point {req['point']}"}
        type_id = p.cmd_type
        if type_id is None:
            return {"ok": False, "stage": "fep", "detail": f"{p.id} is not a control point"}
        try:
            value = command_value(type_id, req["value"])
        except ValueError as e:
            return {"ok": False, "stage": "fep", "detail": str(e)}
        action = req.get("action", "execute")
        if action == "cancel":
            ok = await self.client.cancel_select(type_id, p.ioa, value)
            r = {"ok": ok, "stage": "deactcon", "detail": ""}
        else:
            res = await self.client.command(type_id, p.ioa, value, select=action == "select",
                                            timeout=float(req.get("timeout", 15)))  # fmt: skip
            r = {"ok": res.ok, "stage": res.stage, "detail": res.detail}
        r["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        log.info("command %s %s %s → %s", p.id, action, req["value"], r)
        return r


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "info").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    st = load_station(default_config_path())
    redis = Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
    fep = Fep(st, redis, os.environ.get("RTU_HOST", "127.0.0.1"), int(os.environ.get("RTU_PORT", "2404")))
    log.info("FEP started: %d points, RTU %s:%s, %s", len(st.points), fep.client.host, fep.client.port,
             datetime.now(UTC).isoformat())  # fmt: skip

    async def runner():
        loop = asyncio.get_running_loop()
        task = asyncio.create_task(fep.run())
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, task.cancel)
        try:
            await task
        except asyncio.CancelledError:
            fep.client.stop()
        finally:
            await redis.aclose()

    asyncio.run(runner())
