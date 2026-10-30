"""业务事件总线：告警、告警确认、挂牌变化、遥控进度经 Redis Stream 广播给所有推送连接。

走 Redis 而不是进程内回调：api 多副本时任一副本产生的事件都能推送到连到其他副本的客户端。
"""

import json

from redis.asyncio import Redis

from . import keys

MAXLEN = 2000


async def publish(redis: Redis, msg: dict) -> None:
    await redis.xadd(keys.EV_BUS, {"m": json.dumps(msg, ensure_ascii=False)}, maxlen=MAXLEN, approximate=True)
