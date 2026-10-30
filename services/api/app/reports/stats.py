"""报表统计（纯函数，便于单元测试）。

输入为 1 分钟聚合数据 (分钟时标 ms, avg, min, max)：
    - 电压合格率按 GB/T 惯例以 1 分钟平均值统计：合格时间 /（总运行时间 − 停电时间）
    - 极值取分钟内最大/最小值，并记录出现时刻
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

Row = tuple[int, float, float, float]  # (分钟时标 ms, avg, min, max)


@dataclass
class VoltageStat:
    point: str
    name: str
    lo: float
    hi: float
    minutes: int  # 统计时间（不含停电）
    over: int  # 超上限分钟
    under: int  # 超下限分钟
    outage: int  # 停电分钟
    max: float | None
    max_t: int | None
    min: float | None
    min_t: int | None

    @property
    def rate(self) -> float | None:
        return (
            None
            if self.minutes == 0
            else round(100 * (self.minutes - self.over - self.under) / self.minutes, 3)
        )

    def to_dict(self) -> dict:
        return {**asdict(self), "rate": self.rate}


def voltage_stat(
    point: str, name: str, rows: list[Row], lo: float, hi: float, dead_below: float
) -> VoltageStat:
    s = VoltageStat(point, name, lo, hi, 0, 0, 0, 0, None, None, None, None)
    for t, avg, mn, mx in rows:
        if avg < dead_below:
            s.outage += 1
            continue
        s.minutes += 1
        if avg > hi:
            s.over += 1
        elif avg < lo:
            s.under += 1
        if s.max is None or mx > s.max:
            s.max, s.max_t = mx, t
        # 最低电压不计停电过程中的跌落
        if mn >= dead_below and (s.min is None or mn < s.min):
            s.min, s.min_t = mn, t
    return s


def extreme(rows: list[Row]) -> dict:
    """最大值（分钟内最大）及时刻、平均值（分钟平均的平均）。"""
    if not rows:
        return {"max": None, "max_t": None, "avg": None}
    t, _, _, mx = max(rows, key=lambda r: r[3])
    return {"max": mx, "max_t": t, "avg": sum(r[1] for r in rows) / len(rows)}


def load_stats(curve: list[tuple[int, float]]) -> dict:
    """负荷特性：最大/最小/平均负荷、负荷率（平均/最大）、峰谷差率。"""
    vals = [(t, v) for t, v in curve if v is not None]
    if not vals:
        return {"max": None, "max_t": None, "min": None, "min_t": None, "avg": None, "load_factor": None,
                "peak_valley": None}  # fmt: skip
    tmax, vmax = max(vals, key=lambda x: x[1])
    tmin, vmin = min(vals, key=lambda x: x[1])
    avg = sum(v for _, v in vals) / len(vals)
    return {
        "max": vmax,
        "max_t": tmax,
        "min": vmin,
        "min_t": tmin,
        "avg": avg,
        "load_factor": round(100 * avg / vmax, 2) if vmax > 0 else None,
        "peak_valley": round(100 * (vmax - vmin) / vmax, 2) if vmax > 0 else None,
    }


def locf(rows: list[tuple[int, float, float, float, float]], start: int, end: int, init: float | None,
         step: int = 60_000) -> list[Row]:  # fmt: skip
    """
    补齐分钟序列（前值保持）：历史库只存变化，值不变的分钟没有聚合记录，
    缺失分钟取前一分钟的末值（或区间前最后一个值）。rows 为 (t, avg, min, max, last)，按时间排序。
    """
    have = {r[0]: r for r in rows}
    out: list[Row] = []
    last = init
    for t in range(start - start % step, end, step):
        r = have.get(t)
        if r is not None:
            out.append((t, r[1], r[2], r[3]))
            last = r[4]
        elif last is not None:
            out.append((t, last, last, last))
    return out


def bucket_seconds(span_s: float, max_points: int = 288) -> int:
    """曲线点数控制在 max_points 以内，粒度为整分钟。"""
    return max(60, -(-int(span_s) // max_points // 60) * 60)
