from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from app import audit
from app.alarm.engine import alarm_dict
from app.auth.deps import client_ip, require
from app.auth.security import User
from app.realtime.bus import publish

router = APIRouter(prefix="/api/alarms", tags=["alarms"])


@router.get("", summary="告警查询（倒序）")
async def list_alarms(
    request: Request,
    limit: int = Query(200, ge=1, le=2000),
    level: int | None = Query(None, ge=1, le=5),
    unacked: bool = False,
    before_id: int | None = None,
) -> list[dict]:
    rows = await request.app.state.pool.fetch(
        """SELECT * FROM alarm
           WHERE ($2::int IS NULL OR level = $2) AND (NOT $3 OR ack_ts IS NULL) AND ($4::bigint IS NULL OR id < $4)
           ORDER BY id DESC LIMIT $1""",
        limit, level, unacked, before_id,
    )  # fmt: skip
    return [alarm_dict(r) for r in rows]


@router.get("/active", summary="未确认告警与分级计数（告警窗初始化）")
async def active(request: Request) -> dict:
    rows = await request.app.state.pool.fetch(
        "SELECT * FROM alarm WHERE ack_ts IS NULL ORDER BY id DESC LIMIT 500"
    )
    recent = await request.app.state.pool.fetch(
        "SELECT * FROM alarm WHERE ack_ts IS NOT NULL ORDER BY id DESC LIMIT 100"
    )
    return {"unacked": [alarm_dict(r) for r in rows], "recent": [alarm_dict(r) for r in recent]}


class AckReq(BaseModel):
    ids: list[int] = []
    all: bool = False


@router.post("/ack", summary="确认告警")
async def ack(req: AckReq, request: Request, user: User = Depends(require("ack"))) -> dict:
    st = request.app.state
    rows = await st.pool.fetch(
        "UPDATE alarm SET ack_by = $1, ack_ts = now() WHERE ack_ts IS NULL AND ($3 OR id = ANY($2::bigint[])) "
        "RETURNING id, ack_ts",
        user.name, req.ids, req.all,
    )  # fmt: skip
    ids = [r["id"] for r in rows]
    if ids:
        ts = int(rows[0]["ack_ts"].timestamp() * 1000)
        await publish(st.redis, {"type": "alarm_ack", "ids": ids, "by": user.name, "ts": ts})
        await audit.write(st.pool, "alarm.ack", user=user, target=f"{len(ids)} 条",
                          detail={"ids": ids[:200], "all": req.all}, ip=client_ip(request))  # fmt: skip
    return {"acked": ids}
