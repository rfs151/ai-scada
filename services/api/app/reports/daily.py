"""运行报表：负荷曲线、电压合格率、主变与出线运行统计、电量、告警与操作统计。"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

import asyncpg

from app.reports.stats import Row, bucket_seconds, extreme, load_stats, locf, voltage_stat
from scadalib.model import Cat, Station

DEAD_RATIO = 0.1


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


async def build(pool: asyncpg.Pool, st: Station, start: datetime, end: datetime) -> dict:
    bays = {b.id: b for b in st.bays}
    feeders = [b for b in st.bays if b.kind in ("feeder", "pv")]
    trafos = [b for b in st.bays if b.kind == "transformer"]
    voltage_pts = [p for p in st.points if p.meas == "UAB" and p.limits is not None]
    stat_pts = (
        [p.id for p in voltage_pts]
        + [f"{b.id}.{m}" for b in feeders for m in ("I", "P")]
        + [f"{b.id}.{m}" for b in trafos for m in ("LOAD", "TOIL", "LP")]
    )
    curve_pts = ["L1.P", "L2.P"] + [f"{b.id}.P" for b in feeders]
    minute_pts = sorted(set(stat_pts) | set(curve_pts))
    dd_pts = [p.id for p in st.points if p.cat == Cat.DD]
    tap_pts = [f"{b.id}.TAP" for b in trafos]
    bucket_s = bucket_seconds((end - start).total_seconds())

    async with pool.acquire() as conn:
        minute = await conn.fetch(
            """SELECT point_id, bucket, avg, min, max, last FROM analog_1m
               WHERE point_id = ANY($1::text[]) AND bucket >= $2 AND bucket < $3 ORDER BY bucket""",
            minute_pts, start, end,
        )  # fmt: skip
        # 区间前最后一个值：用于补齐区间开头没有变化记录的分钟
        init = await conn.fetch(
            """SELECT p.id, h.value FROM unnest($1::text[]) AS p(id)
               CROSS JOIN LATERAL (SELECT value FROM analog_hist WHERE point_id = p.id AND ts < $2
                                   AND ts > $2 - interval '1 day' ORDER BY ts DESC LIMIT 1) h""",
            minute_pts, start,
        )  # fmt: skip
        # 电度：区间末值 − 区间初值（电度表读数，与仿真时钟同步累计）
        energy = await conn.fetch(
            """SELECT p.id,
                 (SELECT value FROM analog_hist WHERE point_id = p.id AND ts >= $2 AND ts <= $3 ORDER BY ts LIMIT 1) AS a,
                 (SELECT value FROM analog_hist WHERE point_id = p.id AND ts >= $2 AND ts <= $3
                  ORDER BY ts DESC LIMIT 1) AS b
               FROM unnest($1::text[]) AS p(id)""",
            dd_pts, start, end,
        )  # fmt: skip
        # 分接开关动作次数：相邻两次记录值不同计一次
        taps = await conn.fetch(
            """SELECT point_id, count(*) FILTER (WHERE prev IS NOT NULL AND value <> prev) AS n FROM (
                 SELECT point_id, value, lag(value) OVER (PARTITION BY point_id ORDER BY ts) AS prev
                 FROM analog_hist WHERE point_id = ANY($1::text[]) AND ts >= $2 AND ts < $3) x
               GROUP BY point_id""",
            tap_pts, start, end,
        )  # fmt: skip
        trips = await conn.fetch(
            "SELECT bay, count(*) AS n FROM alarm WHERE kind = '事故跳闸' AND ts >= $1 AND ts < $2 GROUP BY bay",
            start, end,
        )  # fmt: skip
        levels = await conn.fetch(
            "SELECT level, count(*) AS n FROM alarm WHERE ts >= $1 AND ts < $2 GROUP BY level", start, end
        )
        ops = await conn.fetch(
            """SELECT action, count(*) FILTER (WHERE ok) AS ok, count(*) FILTER (WHERE NOT ok) AS fail FROM audit_log
               WHERE ts >= $1 AND ts < $2 AND action IN ('control.execute', 'control.adjust', 'tag.add', 'sim.inject')
               GROUP BY action""",
            start, end,
        )  # fmt: skip
        pdr = await conn.fetchval("SELECT count(*) FROM pdr WHERE ts >= $1 AND ts < $2", start, end)

    raw: dict[str, list] = defaultdict(list)
    for r in minute:
        raw[r["point_id"]].append((_ms(r["bucket"]), r["avg"], r["min"], r["max"], r["last"]))
    init_v = {r["id"]: r["value"] for r in init}
    s_ms, e_ms = _ms(start), _ms(end)
    by_point: dict[str, list[Row]] = defaultdict(list)
    for pid in minute_pts:
        by_point[pid] = locf(raw[pid], s_ms, e_ms, init_v.get(pid))

    # ---- 负荷曲线：补齐后的分钟平均值再按曲线粒度平均
    step = bucket_s * 1000
    acc: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for pid in curve_pts:
        for t, avg, _, _ in by_point[pid]:
            acc[t - t % step][pid].append(avg)
    buckets = {t: {pid: sum(v) / len(v) for pid, v in pts.items()} for t, pts in acc.items()}
    load_ids = [f"{b.id}.P" for b in feeders if b.kind == "feeder"]
    pv_ids = [f"{b.id}.P" for b in feeders if b.kind == "pv"]
    curve = [
        {
            "t": t,
            "import": _sum(v, ["L1.P", "L2.P"]),
            "load": _sum(v, load_ids),
            "pv": _sum(v, pv_ids),
        }
        for t, v in sorted(buckets.items())
    ]

    # ---- 电压合格率
    voltage = []
    for p in voltage_pts:
        vn = float(st.voltage_levels[bays[p.bay].vl]["vn_kv"])
        s = voltage_stat(p.id, p.name, by_point[p.id], p.limits.lo, p.limits.hi, vn * DEAD_RATIO)
        voltage.append(s.to_dict())

    e = {r["id"]: (r["b"] - r["a"]) if r["a"] is not None and r["b"] is not None else None for r in energy}
    tap_n = {r["point_id"]: r["n"] for r in taps}
    trip_n = {r["bay"]: r["n"] for r in trips}

    transformers = []
    for b in trafos:
        load, oil = extreme(by_point[f"{b.id}.LOAD"]), extreme(by_point[f"{b.id}.TOIL"])
        transformers.append({
            "bay": b.id, "name": b.name,
            "load_max": load["max"], "load_max_t": load["max_t"], "load_avg": load["avg"],
            "oil_max": oil["max"], "oil_max_t": oil["max_t"],
            "tap_ops": tap_n.get(f"{b.id}.TAP", 0),
            "energy_kwh": e.get(f"{b.id}.E_IMP"),
        })  # fmt: skip

    feeder_rows = []
    for b in feeders:
        i, p = extreme(by_point[f"{b.id}.I"]), extreme(by_point[f"{b.id}.P"])
        lim = st.point(f"{b.id}.I").limits
        feeder_rows.append({
            "bay": b.id, "name": b.name, "kind": b.kind,
            "i_max": i["max"], "i_max_t": i["max_t"], "i_limit": lim.hi if lim else None,
            "p_max": p["max"], "p_avg": p["avg"],
            "energy_kwh": e.get(f"{b.id}.E_EXP" if b.kind == "pv" else f"{b.id}.E_IMP"),
            "trips": trip_n.get(b.id, 0),
        })  # fmt: skip

    return {
        "start": _ms(start),
        "end": _ms(end),
        "bucket_s": bucket_s,
        "load": load_stats([(c["t"], c["load"]) for c in curve]),
        "import": load_stats([(c["t"], c["import"]) for c in curve]),
        "curve": curve,
        "voltage": voltage,
        "transformers": transformers,
        "feeders": feeder_rows,
        "alarms": {int(r["level"]): r["n"] for r in levels},
        "operations": {r["action"]: {"ok": r["ok"], "fail": r["fail"]} for r in ops},
        "pdr": pdr,
    }


def _sum(v: dict[str, float], ids: list[str]) -> float | None:
    xs = [v[i] for i in ids if i in v]
    return sum(xs) if xs else None
