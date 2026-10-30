"""实时推送通道。

客户端 → 服务端:
    {"type": "subscribe", "points": ["F01.P", ...] | "*"}   按画面订阅，返回快照后推送增量
    {"type": "ping", "ts": <ms>}                            往返时延测量
服务端 → 客户端:
    hello / heartbeat / pong
    snapshot  {"values": {点号: {v,q,t}}}
    rt        {"values": {...}}            合帧增量（200ms）
    yx        {"events": [...]}            遥信变位
    channel   {"channels": {...}}          通道状态（2s）
    alarm / alarm_ack / tags / control    业务事件（见 app/realtime/bus.py）
"""

import asyncio
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()
log = logging.getLogger(__name__)

MAX_SUBSCRIBE = 2000


@router.websocket("/ws/realtime")
async def realtime(ws: WebSocket) -> None:
    await ws.accept()
    settings = ws.app.state.settings
    hub = ws.app.state.hub
    client = hub.add(ws)
    await client.send({"type": "hello", "version": settings.app_version})
    seq = 0
    try:
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=settings.ws_heartbeat_s)
            except TimeoutError:
                seq += 1
                await client.send(
                    {"type": "heartbeat", "seq": seq, "server_time": datetime.now(UTC).isoformat()}
                )
                continue
            match msg.get("type"):
                case "ping":
                    # 回显客户端时间戳，便于前端计算往返时延
                    await client.send({"type": "pong", "client_ts": msg.get("ts")})
                case "subscribe":
                    pts = msg.get("points", [])
                    if pts != "*" and (not isinstance(pts, list) or len(pts) > MAX_SUBSCRIBE):
                        await client.send({"type": "error", "detail": "invalid subscription"})
                        continue
                    await hub.subscribe(client, pts)
    except WebSocketDisconnect:
        log.debug("ws client disconnected")
    finally:
        hub.remove(client)
