"""实时数据、历史曲线、遥信事件、通道与报文。"""

import json
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request

from app.realtime import keys
from app.realtime.hub import read_channels

router = APIRouter(prefix="/api", tags=["data"])

RAW_MAX_SPAN = timedelta(hours=3)
RAW_ROW_LIMIT = 20_000


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


@router.get("/realtime", summary="实时值快照")
async def realtime(request: Request, ids: str | None = Query(None, description="逗号分隔的点号，缺省为全部")):
    redis = request.app.state.redis
    if ids:
        id_list = [i for i in ids.split(",") if i]
        vals = await redis.hmget(keys.RT_VALUES, id_list)
        raw = {pid: v for pid, v in zip(id_list, vals, strict=True) if v is not None}
    else:
        raw = await redis.hgetall(keys.RT_VALUES)
    return {pid: json.loads(v) for pid, v in raw.items()}


@router.get("/history/{point_id}", summary="历史曲线：短时段返回原始变化数据，长时段返回聚合数据")
async def history(
    request: Request,
    point_id: str,
    start: datetime | None = None,
    end: datetime | None = None,
    max_points: int = Query(800, ge=50, le=5000),
):
    st = request.app.state.station
    try:
        p = st.point(point_id)
    except KeyError:
        raise HTTPException(404, f"unknown point {point_id}") from None
    end = end or datetime.now(UTC)
    start = start or end - timedelta(hours=1)
    if start >= end:
        raise HTTPException(400, "start must be before end")

    pool = request.app.state.pool
    async with pool.acquire() as conn:
        if end - start <= RAW_MAX_SPAN:
            rows = await conn.fetch(
                "SELECT ts, value FROM analog_hist WHERE point_id = $1 AND ts >= $2 AND ts <= $3 "
                "ORDER BY ts LIMIT $4",
                point_id, start, end, RAW_ROW_LIMIT,
            )  # fmt: skip
            # 变化存储：区间起点的值 = 起点之前最后一个值，补在开头保证阶梯曲线从起点开始
            prev = await conn.fetchrow(
                "SELECT ts, value FROM analog_hist WHERE point_id = $1 AND ts < $2 ORDER BY ts DESC LIMIT 1",
                point_id, start,
            )  # fmt: skip
            data = ([[_ms(start), prev["value"]]] if prev else []) + [
                [_ms(r["ts"]), r["value"]] for r in rows
            ]
            return {"point": p.to_dict(), "mode": "raw", "start": _ms(start), "end": _ms(end), "data": data}

        bucket_s = max(60, int((end - start).total_seconds() / max_points) // 60 * 60)
        rows = await conn.fetch(
            "SELECT time_bucket(make_interval(secs => $4), bucket) AS b, avg(avg) AS avg, min(min) AS min, "
            "max(max) AS max FROM analog_1m WHERE point_id = $1 AND bucket >= $2 AND bucket <= $3 "
            "GROUP BY b ORDER BY b",
            point_id, start, end, bucket_s,
        )  # fmt: skip
        data = [[_ms(r["b"]), r["avg"], r["min"], r["max"]] for r in rows]
        return {"point": p.to_dict(), "mode": "agg", "bucket_s": bucket_s, "start": _ms(start), "end": _ms(end),
                "data": data}  # fmt: skip


@router.get("/events/yx", summary="遥信变位 / SOE（按设备时标倒序）")
async def yx_events(request: Request, limit: int = Query(200, ge=1, le=2000), point: str | None = None):
    async with request.app.state.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT e.id, e.dev_ts, e.recv_ts, e.point_id, e.value, e.old_value, e.cot, m.name, m.double, m.bay
            FROM yx_event e JOIN measurement m ON m.id = e.point_id
            WHERE ($2::text IS NULL OR e.point_id = $2)
            ORDER BY coalesce(e.dev_ts, e.recv_ts) DESC, e.id DESC
            LIMIT $1
            """,
            limit, point,
        )  # fmt: skip
    return [
        {
            "id": r["id"],
            "dev_t": _ms(r["dev_ts"]) if r["dev_ts"] else None,
            "recv_t": _ms(r["recv_ts"]),
            "p": r["point_id"],
            "name": r["name"],
            "bay": r["bay"],
            "double": r["double"],
            "v": r["value"],
            "old": r["old_value"],
            "cot": r["cot"],
        }
        for r in rows
    ]


@router.get("/channels", summary="通道状态与统计")
async def channels(request: Request):
    return await read_channels(request.app.state.redis)


@router.get("/channels/{channel}/frames", summary="报文监视")
async def frames(
    request: Request,
    channel: str,
    after: str | None = Query(None, description="上次返回的最后一条 id，增量获取"),
    limit: int = Query(200, ge=1, le=1000),
):
    redis = request.app.state.redis
    if after:
        entries = await redis.xrange(keys.FRAMES, min=f"({after}", count=limit)
    else:
        entries = list(reversed(await redis.xrevrange(keys.FRAMES, count=limit)))
    return [
        {"id": eid, "dir": f["dir"], "hex": f["hex"], "text": f["text"], "t": int(f["t"])}
        for eid, f in entries
        if f.get("ch") == channel
    ]
