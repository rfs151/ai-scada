"""演示环境自动复位：定时恢复正常运行方式，避免公开演示站被访客留在故障状态。

有人操作（遥控、场景注入、挂牌）后推迟到空闲 idle_min 分钟再复位，不打断正在演示的人。
复位内容：仿真子站恢复正常方式、摘除全部标识牌、确认全部告警，并写审计日志。
"""

from __future__ import annotations

import asyncio
import logging
import time

from app import audit, sim_control
from app.realtime.bus import publish

log = logging.getLogger(__name__)
CHECK_S = 30


def decide(now: float, next_at: float, last_activity: float | None, interval_s: float, idle_s: float):
    """返回 (是否复位, 下次检查时刻)。时刻均为秒。"""
    if now < next_at:
        return False, next_at
    if last_activity is not None and now - last_activity < idle_s:
        return False, last_activity + idle_s
    return True, now + interval_s


class AutoReset:
    def __init__(self, state, interval_min: float, idle_min: float = 10, clock=time.time):
        self.state, self.clock = state, clock
        self.interval_s, self.idle_s = interval_min * 60, idle_min * 60
        self.next_at = clock() + self.interval_s
        self.last: dict | None = None

    @property
    def enabled(self) -> bool:
        return self.interval_s > 0

    def info(self) -> dict:
        return {"enabled": self.enabled, "interval_min": self.interval_s / 60, "idle_min": self.idle_s / 60,
                "next_at": int(self.next_at * 1000) if self.enabled else None, "last": self.last}  # fmt: skip

    async def run(self) -> None:
        while True:
            await asyncio.sleep(CHECK_S)
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("auto reset failed")

    async def check(self) -> bool:
        last = await self.state.pool.fetchval(
            """SELECT extract(epoch FROM max(ts)) FROM audit_log
               WHERE action LIKE 'control.%' OR action LIKE 'sim.%' OR action LIKE 'tag.%'"""
        )
        do, self.next_at = decide(self.clock(), self.next_at, float(last) if last else None, self.interval_s,
                                  self.idle_s)  # fmt: skip
        if do:
            await self.reset()
        return do

    async def reset(self) -> dict:
        st = self.state
        res = await sim_control.send(st.redis, "reset")
        tags_removed = await st.tags.clear()
        rows = await st.pool.fetch(
            "UPDATE alarm SET ack_by = '自动复位', ack_ts = now() WHERE ack_ts IS NULL RETURNING id, ack_ts"
        )
        if rows:
            ts = int(rows[0]["ack_ts"].timestamp() * 1000)
            await publish(
                st.redis, {"type": "alarm_ack", "ids": [r["id"] for r in rows], "by": "自动复位", "ts": ts}
            )
        detail = {"sim": res["detail"], "tags_removed": tags_removed, "alarms_acked": len(rows)}
        await audit.write(st.pool, "system.reset", username="系统", target="恢复正常运行方式", ok=res["ok"],
                          detail=detail)  # fmt: skip
        self.last = {"t": int(self.clock() * 1000), **detail}
        log.info("auto reset: %s", detail)
        return detail
