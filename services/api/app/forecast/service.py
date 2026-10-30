"""负荷预测服务：从历史库取 10kV 负荷（各出线有功之和），按仿真日对齐后预测。"""

from __future__ import annotations

from datetime import UTC, datetime

import asyncpg

from app.forecast.model import K_DAYS, SLOTS_PER_DAY, accuracy, forecast
from app.reports.stats import locf
from scadalib import simclock
from scadalib.model import Station


async def load_history(
    pool: asyncpg.Pool, st: Station, start: float, end: float, step_s: float
) -> dict[int, float]:
    """[start, end) 的负荷序列，按 step_s 分段平均，键为全局时段序号 round(t / step_s)。"""
    pts = [f"{b.id}.P" for b in st.bays if b.kind == "feeder"]
    t0, t1 = datetime.fromtimestamp(start, UTC), datetime.fromtimestamp(end, UTC)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT point_id, time_bucket(make_interval(secs => $4), ts) AS b, avg(value) AS v, last(value, ts) AS l
               FROM analog_hist WHERE point_id = ANY($1::text[]) AND ts >= $2 AND ts < $3
               GROUP BY point_id, b ORDER BY b""",
            pts, t0, t1, step_s,
        )  # fmt: skip
        init = await conn.fetch(
            """SELECT p.id, h.value FROM unnest($1::text[]) AS p(id)
               CROSS JOIN LATERAL (SELECT value FROM analog_hist WHERE point_id = p.id AND ts < $2
                                   AND ts > $2 - interval '1 day' ORDER BY ts DESC LIMIT 1) h""",
            pts, t0,
        )  # fmt: skip
    raw: dict[str, list] = {p: [] for p in pts}
    for r in rows:
        t = int(r["b"].timestamp() * 1000)
        raw[r["point_id"]].append((t, r["v"], r["v"], r["v"], r["l"]))
    init_v = {r["id"]: r["value"] for r in init}
    step_ms = int(step_s * 1000)
    total: dict[int, float] = {}
    count: dict[int, int] = {}
    for p in pts:
        for t, avg, _, _ in locf(raw[p], int(start * 1000), int(end * 1000), init_v.get(p), step=step_ms):
            s = round(t / step_ms)
            total[s] = total.get(s, 0.0) + avg
            count[s] = count.get(s, 0) + 1
    # 只保留全部出线都有值的时段
    return {s: v for s, v in total.items() if count[s] == len(pts)}


async def build(pool: asyncpg.Pool, st: Station, speed: float, now: float) -> dict:
    day_s = simclock.day_seconds(speed)
    step = day_s / SLOTS_PER_DAY
    today = simclock.day_start(now, speed)
    now_slot = int(now // step)  # 当前未完成时段
    hist = await load_history(pool, st, today - (K_DAYS + 1) * day_s, now_slot * step, step)

    def ts(s: int) -> int:
        return int(s * step * 1000)

    # 实测：最近 1 个仿真日；预测：从当前时段起 1 个仿真日（含实时修正）
    actual_from = now_slot - SLOTS_PER_DAY
    actual = [{"t": ts(s), "v": hist.get(s)} for s in range(actual_from, now_slot)]
    fc = forecast(hist, now_slot, SLOTS_PER_DAY)
    # 回测：上一个完整仿真日的日前预测（不修正）与实测对比
    y0 = round((today - day_s) / step)
    y_fc = forecast(hist, y0, SLOTS_PER_DAY, corrected=False)
    y_act = [hist.get(s) for s in range(y0, y0 + SLOTS_PER_DAY)]
    days = len({(s - y0) // SLOTS_PER_DAY for s in hist if s < y0})
    return {
        "now": int(now * 1000),
        "step_s": step,
        "day_s": day_s,
        "history_days": days,
        "actual": actual,
        "forecast": [{"t": ts(now_slot + i), "v": v} for i, v in enumerate(fc)],
        "backtest": {
            "day_start": ts(y0),
            "series": [
                {"t": ts(y0 + i), "actual": a, "forecast": f}
                for i, (a, f) in enumerate(zip(y_act, y_fc, strict=True))
            ],
            "metrics": accuracy(y_act, y_fc),
        },
    }
