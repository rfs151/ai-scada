"""从实时库读取开关位置（五防校验用）。品质无效或非刷新的位置视为未知。"""

import json
from collections.abc import Callable

from redis.asyncio import Redis

from app.realtime import keys
from scadalib.iec104 import Quality
from scadalib.model import Station

BAD = Quality.IV | Quality.NT


async def read_positions(redis: Redis, st: Station) -> Callable[[str], int | None]:
    ids = [e.id for e in st.equipment if e.is_switch]
    raw = await redis.hmget(keys.RT_VALUES, [f"{i}.POS" for i in ids])
    pos: dict[str, int | None] = {}
    for eid, r in zip(ids, raw, strict=True):
        if r is None:
            pos[eid] = None
            continue
        v = json.loads(r)
        pos[eid] = None if v["q"] & BAD else int(v["v"])
    return pos.get
