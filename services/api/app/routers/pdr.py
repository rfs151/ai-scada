"""事故追忆（PDR）：从历史库还原事故前后全站断面与变化序列，供接线图回放。

回放数据 = 起始断面（每个测点在窗口开始时刻的值）+ 窗口内按时间排序的全部变化。
前端从断面出发逐条应用变化，即可得到任意时刻的全站状态。
"""

import json
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request

from app.realtime import keys
from scadalib.model import Cat

router = APIRouter(prefix="/api/pdr", tags=["pdr"])


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _pdr_dict(r) -> dict:
    return {"id": r["id"], "ts": _ms(r["ts"]), "title": r["title"], "bay": r["bay"],
            "pre_s": r["pre_s"], "post_s": r["post_s"]}  # fmt: skip


@router.get("", summary="事故追忆列表")
async def list_pdr(request: Request, limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    rows = await request.app.state.pool.fetch("SELECT * FROM pdr ORDER BY ts DESC LIMIT $1", limit)
    return [_pdr_dict(r) for r in rows]


@router.get("/{pdr_id}", summary="事故追忆回放数据")
async def replay(pdr_id: int, request: Request) -> dict:
    st = request.app.state
    r = await st.pool.fetchrow("SELECT * FROM pdr WHERE id = $1", pdr_id)
    if r is None:
        raise HTTPException(404, "追忆记录不存在")
    start = r["ts"] - timedelta(seconds=r["pre_s"])
    end = min(r["ts"] + timedelta(seconds=r["post_s"]), datetime.now(UTC))
    yc = [p.id for p in st.station.points if p.cat == Cat.YC]
    yx = [p.id for p in st.station.points if p.cat == Cat.YX]

    async with st.pool.acquire() as conn:
        # 起始断面：每点取窗口开始前最后一个值（索引 point_id, ts DESC，逐点 LIMIT 1）
        a0 = await conn.fetch(
            """SELECT p.id, h.value, h.quality FROM unnest($1::text[]) AS p(id)
               CROSS JOIN LATERAL (SELECT value, quality FROM analog_hist
                                   WHERE point_id = p.id AND ts <= $2 AND ts > $2 - interval '1 day'
                                   ORDER BY ts DESC LIMIT 1) h""",
            yc, start,
        )  # fmt: skip
        changes = await conn.fetch(
            """SELECT ts, point_id, value, quality FROM analog_hist
               WHERE ts > $1 AND ts <= $2 AND point_id = ANY($3::text[]) ORDER BY ts""",
            start, end, yc,
        )  # fmt: skip
        y0 = await conn.fetch(
            """SELECT p.id, e.value FROM unnest($1::text[]) AS p(id)
               CROSS JOIN LATERAL (SELECT value FROM yx_event WHERE point_id = p.id AND recv_ts <= $2
                                   ORDER BY recv_ts DESC LIMIT 1) e""",
            yx, start,
        )  # fmt: skip
        events = await conn.fetch(
            """SELECT e.id, e.dev_ts, e.recv_ts, e.point_id, e.value, e.old_value, e.cot, m.name, m.double, m.bay
               FROM yx_event e JOIN measurement m ON m.id = e.point_id
               WHERE coalesce(e.dev_ts, e.recv_ts) > $1 AND coalesce(e.dev_ts, e.recv_ts) <= $2
               ORDER BY coalesce(e.dev_ts, e.recv_ts), e.id""",
            start, end,
        )  # fmt: skip

    init: dict[str, list] = {x["id"]: [x["value"], x["quality"]] for x in a0}
    init.update({x["id"]: [x["value"], 0] for x in y0})
    # 窗口前没有记录的遥信：取窗口内第一次变位的旧值；仍没有则取当前值（期间未变化）
    for e in events:
        if e["point_id"] not in init and e["old_value"] is not None:
            init[e["point_id"]] = [e["old_value"], 0]
    missing = [p for p in yc + yx if p not in init]
    if missing:
        for pid, raw in zip(missing, await st.redis.hmget(keys.RT_VALUES, missing), strict=True):
            if raw:
                v = json.loads(raw)
                init[pid] = [v["v"], v["q"]]

    return {
        "pdr": _pdr_dict(r),
        "start": _ms(start),
        "end": _ms(end),
        "init": init,
        "analog": [[_ms(c["ts"]), c["point_id"], c["value"], c["quality"]] for c in changes],
        "events": [
            {
                "id": e["id"],
                "dev_t": _ms(e["dev_ts"]) if e["dev_ts"] else None,
                "recv_t": _ms(e["recv_ts"]),
                "p": e["point_id"],
                "name": e["name"],
                "bay": e["bay"],
                "double": e["double"],
                "v": e["value"],
                "old": e["old_value"],
                "cot": e["cot"],
            }
            for e in events
        ],
    }
