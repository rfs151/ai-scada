"""仿真控制通道客户端（与 rtu-sim/rtusim/control.py 对应）。"""

import json
import uuid

from redis.asyncio import Redis

CMD_KEY, STATE_KEY = "sim:cmd", "sim:state"


async def state(redis: Redis) -> dict | None:
    raw = await redis.get(STATE_KEY)
    return json.loads(raw) if raw else None


async def send(redis: Redis, scenario: str, target: str | None = None, timeout: int = 5) -> dict:
    """下发场景并等待仿真子站回执 {"ok", "detail"}。"""
    rid = uuid.uuid4().hex
    await redis.lpush(CMD_KEY, json.dumps({"id": rid, "scenario": scenario, "target": target}))
    item = await redis.blpop([f"sim:res:{rid}"], timeout=timeout)
    return json.loads(item[1]) if item else {"ok": False, "detail": "仿真子站无响应"}
