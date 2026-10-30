"""实时推送中心：一个 Redis 读取任务 → 多个 WebSocket 客户端。

- 客户端按画面订阅测点（或 "*" 全部），只推送订阅范围内的变化
- 变化先在每个客户端缓冲区合并（同一测点只保留最新值），每 FLUSH_S 发送一帧，避免推送风暴
- 遥信变位事件、通道状态、业务事件总线（告警/挂牌/遥控进度）广播给所有客户端
- 慢客户端：缓冲区以测点为键，天然有上限（≤ 测点数），不会无限堆积
"""

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field

from fastapi import WebSocket
from redis.asyncio import Redis

from . import keys

log = logging.getLogger(__name__)

FLUSH_S = 0.2
CHANNEL_POLL_S = 2.0


@dataclass(eq=False)
class Client:
    ws: WebSocket
    points: set[str] | None = field(default_factory=set)  # None = 全部
    pending: dict[str, dict] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    bus: list[dict] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def wants(self, pid: str) -> bool:
        return self.points is None or pid in self.points

    async def send(self, msg: dict) -> None:
        async with self.lock:
            await self.ws.send_json(msg)


class Hub:
    def __init__(self, redis: Redis):
        self.redis = redis
        self.clients: set[Client] = set()
        self.stats = {"changes": 0, "frames_sent": 0}

    # ------------------------------------------------------------ 客户端

    def add(self, ws: WebSocket) -> Client:
        c = Client(ws)
        self.clients.add(c)
        return c

    def remove(self, c: Client) -> None:
        self.clients.discard(c)

    async def subscribe(self, c: Client, points: list[str] | str) -> None:
        c.points = None if points == "*" else set(points)
        if c.points is None:
            raw = await self.redis.hgetall(keys.RT_VALUES)
        else:
            ids = sorted(c.points)
            vals = await self.redis.hmget(keys.RT_VALUES, ids) if ids else []
            raw = {pid: v for pid, v in zip(ids, vals, strict=True) if v is not None}
        await c.send({"type": "snapshot", "values": {pid: json.loads(v) for pid, v in raw.items()}})

    # ------------------------------------------------------------ 后台任务

    async def run(self) -> None:
        await asyncio.gather(self._read_loop(), self._flush_loop(), self._channel_loop())

    async def _start_ids(self) -> dict[str, str]:
        # 不用 "$"：某个流本轮无数据时下一轮仍从 "$" 读，两次调用之间到达的消息会丢失
        out = {}
        for s in (keys.RT_CHANGES, keys.YX_EVENTS, keys.EV_BUS):
            last = await self.redis.xrevrange(s, count=1)
            out[s] = last[0][0] if last else "0-0"
        return out

    async def _read_loop(self) -> None:
        last: dict[str, str] | None = None
        while True:
            try:
                last = last or await self._start_ids()
                resp = await self.redis.xread(last, count=500, block=1000)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("hub read error: %s", e)
                await asyncio.sleep(2)
                continue
            for stream, entries in resp or []:
                last[stream] = entries[-1][0]
                if stream == keys.RT_CHANGES:
                    for _, f in entries:
                        for pid, v, q, t in json.loads(f["d"]):
                            self.stats["changes"] += 1
                            upd = {"v": v, "q": q, "t": t}
                            for c in self.clients:
                                if c.wants(pid):
                                    c.pending[pid] = upd
                elif stream == keys.YX_EVENTS:
                    evts = [json.loads(f["e"]) for _, f in entries]
                    for c in self.clients:
                        c.events.extend(evts)
                else:
                    msgs = [json.loads(f["m"]) for _, f in entries]
                    for c in self.clients:
                        c.bus.extend(msgs)

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_S)
            for c in list(self.clients):
                if not c.pending and not c.events and not c.bus:
                    continue
                values, c.pending = c.pending, {}
                events, c.events = c.events, []
                msgs, c.bus = c.bus, []
                try:
                    if values:
                        await c.send({"type": "rt", "values": values})
                    if events:
                        await c.send({"type": "yx", "events": events})
                    for m in msgs:  # 告警、挂牌、遥控进度：原样转发
                        await c.send(m)
                    self.stats["frames_sent"] += 1
                except Exception:  # noqa: BLE001 - 连接已断开，由 WS 处理函数清理
                    self.remove(c)

    async def _channel_loop(self) -> None:
        while True:
            await asyncio.sleep(CHANNEL_POLL_S)
            if not self.clients:
                continue
            with contextlib.suppress(Exception):
                chans = await read_channels(self.redis)
                for c in list(self.clients):
                    with contextlib.suppress(Exception):
                        await c.send({"type": "channel", "channels": chans})


async def read_channels(redis: Redis) -> dict[str, dict]:
    out = {}
    async for key in redis.scan_iter(match=f"{keys.CHANNEL_PREFIX}*", count=100):
        h = await redis.hgetall(key)
        out[key.removeprefix(keys.CHANNEL_PREFIX)] = {k: int(v) if v.lstrip("-").isdigit() else v
                                                       for k, v in h.items()}  # fmt: skip
    return out
