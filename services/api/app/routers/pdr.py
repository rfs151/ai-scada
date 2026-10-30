"""事故追忆（PDR）：从历史库还原事故前后全站断面与变化序列，供接线图回放。

回放数据 = 起始断面（每个测点在窗口开始时刻的值）+ 窗口内按时间排序的全部变化。
前端从断面出发逐条应用变化，即可得到任意时刻的全站状态。
"""

import json
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app import audit
from app.auth.deps import client_ip, optional_user
from app.auth.security import User
from app.brief import facts as brief_facts
from app.brief import llm as brief_llm
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


async def get_pdr(st, pdr_id: int):
    r = await st.pool.fetchrow("SELECT * FROM pdr WHERE id = $1", pdr_id)
    if r is None:
        raise HTTPException(404, "追忆记录不存在")
    return r


@router.get("/{pdr_id}", summary="事故追忆回放数据")
async def replay(pdr_id: int, request: Request) -> dict:
    st = request.app.state
    return await load_replay(st, await get_pdr(st, pdr_id))


async def load_replay(st, r) -> dict:
    """回放数据 = 起始断面 + 窗口内全部遥测变化与遥信事件（事故简报也基于这份数据）。"""
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


# ---------------------------------------------------------------- 事故简报

LLM_COOLDOWN_S = 30
_last_llm: dict[int, float] = {}


async def _save_brief(st, pdr_id: int, brief: dict) -> dict:
    await st.pool.execute(
        "UPDATE pdr SET brief = $2 WHERE id = $1", pdr_id, json.dumps(brief, ensure_ascii=False)
    )
    return brief


@router.get("/{pdr_id}/brief", summary="事故简报（首次查看时按规则生成并缓存）")
async def get_brief(pdr_id: int, request: Request) -> dict:
    st = request.app.state
    r = await get_pdr(st, pdr_id)
    if r["brief"]:
        brief = json.loads(r["brief"]) if isinstance(r["brief"], str) else r["brief"]
    else:
        f = brief_facts.extract(st.station, await load_replay(st, r))
        brief = await _save_brief(st, pdr_id, {"source": "template", "text": brief_facts.render(f), "facts": f,
                                              "model": None, "at": _ms(datetime.now(UTC))})  # fmt: skip
    return brief | {"llm_available": brief_llm.available()}


class BriefReq(BaseModel):
    llm: bool = True


@router.post("/{pdr_id}/brief", summary="重新生成事故简报（llm=true 时由大模型撰写）")
async def regen_brief(
    pdr_id: int, req: BriefReq, request: Request, user: User | None = Depends(optional_user)
) -> dict:
    st = request.app.state
    if user is None:
        raise HTTPException(401, "请先登录")
    r = await get_pdr(st, pdr_id)
    f = brief_facts.extract(st.station, await load_replay(st, r))
    brief = {"source": "template", "text": brief_facts.render(f), "facts": f, "model": None}
    if req.llm:
        if not brief_llm.available():
            raise HTTPException(400, "未配置 ANTHROPIC_API_KEY，只能使用规则模板")
        now = datetime.now(UTC).timestamp()
        if now - _last_llm.get(pdr_id, 0) < LLM_COOLDOWN_S:
            raise HTTPException(429, f"同一事故 {LLM_COOLDOWN_S} 秒内只能生成一次")
        _last_llm[pdr_id] = now
        try:
            text, model = await brief_llm.write(f, st.settings.brief_model)
        except brief_llm.BriefError as e:
            await audit.write(st.pool, "pdr.brief", user=user, target=r["title"], ok=False,
                              detail={"pdr": pdr_id, "error": str(e)}, ip=client_ip(request))  # fmt: skip
            raise HTTPException(502, str(e)) from e
        brief |= {"source": "llm", "text": text, "model": model}
    brief["at"] = _ms(datetime.now(UTC))
    await audit.write(st.pool, "pdr.brief", user=user, target=r["title"], detail={"pdr": pdr_id, "source": brief["source"]},
                      ip=client_ip(request))  # fmt: skip
    return await _save_brief(st, pdr_id, brief) | {"llm_available": brief_llm.available()}
