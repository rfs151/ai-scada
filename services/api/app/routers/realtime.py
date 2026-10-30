"""实时推送通道。

P0：仅心跳（服务器时间 + 序号），用于验证 Caddy → api 的 WebSocket 链路和前端断线重连。
P1 起：客户端发送 {"type":"subscribe","points":[...]} 按画面订阅，服务端合帧增量推送。
"""

import asyncio
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()
log = logging.getLogger(__name__)


@router.websocket("/ws/realtime")
async def realtime(ws: WebSocket) -> None:
    await ws.accept()
    settings = ws.app.state.settings
    await ws.send_json({"type": "hello", "version": settings.app_version})
    seq = 0
    try:
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=settings.ws_heartbeat_s)
            except TimeoutError:
                seq += 1
                await ws.send_json(
                    {"type": "heartbeat", "seq": seq, "server_time": datetime.now(UTC).isoformat()}
                )
                continue
            if msg.get("type") == "ping":
                # 回显客户端时间戳，便于前端计算往返时延
                await ws.send_json({"type": "pong", "client_ts": msg.get("ts")})
    except WebSocketDisconnect:
        log.debug("ws client disconnected")
