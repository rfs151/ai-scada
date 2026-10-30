"""短期负荷预测：相似日法 + 实时偏差修正（纯计算，无 I/O）。

相似日法是调度短期负荷预测的经典方法：同一时刻的负荷在相邻日之间高度相关。
    基准预测 F(t) = Σ w_k · L(t − k·日)，k = 1..K，近日权重大（w_k ∝ DECAY^(k-1)）
    异常剔除：与各日同时刻中位数偏差 > OUTLIER 的值不参与（事故停电、检修转供等）
    实时修正：用最近 CORR_SLOTS 个时段"实测 / 基准"的比值 α 修正，随预测步长指数衰减回 1
              F'(t+h) = F(t+h) · (1 + (α − 1) · e^(−h/τ))，α 限幅在 1 ± 15%
准确率按调度考核口径：A = 1 − √( mean(((实际 − 预测) / 实际)²) )

仿真负荷没有气象、节假日等外部特征，梯度提升树（LightGBM）等模型在这里只能学到同样的
"同时刻历史值"关系；接口按 (历史序列 → 预测序列) 设计，接入真实数据和特征后可替换模型。
"""

from __future__ import annotations

import math
from statistics import median

SLOTS_PER_DAY = 96  # 15 分钟一点（仿真时间）
K_DAYS = 7
DECAY = 0.7
OUTLIER = 0.25
CORR_SLOTS = 4  # 最近 1 小时
TAU_SLOTS = 8  # 修正影响约 2 小时
ALPHA_LIMIT = 0.15  # 修正限幅 ±15%：坏数据或事故停电不应把整条预测带偏


def similar_day(hist: dict[int, float], slot: int, k_days: int = K_DAYS) -> float | None:
    """slot 为全局时段序号（自某原点起每 15 分钟加 1）；hist 为 时段序号 → 实测值。"""
    vals = [
        (k, hist[slot - k * SLOTS_PER_DAY]) for k in range(1, k_days + 1) if slot - k * SLOTS_PER_DAY in hist
    ]
    if not vals:
        return None
    m = median(v for _, v in vals)
    keep = [(k, v) for k, v in vals if m == 0 or abs(v - m) <= OUTLIER * abs(m)] or vals
    ws = [DECAY ** (k - 1) for k, _ in keep]
    return sum(w * v for w, (_, v) in zip(ws, keep, strict=True)) / sum(ws)


def correction(hist: dict[int, float], now_slot: int) -> float:
    """最近 CORR_SLOTS 个已完成时段 实测 / 基准 的比值（无数据时为 1）。"""
    num = den = 0.0
    for s in range(now_slot - CORR_SLOTS, now_slot):
        base = similar_day(hist, s)
        if s in hist and base:
            num += hist[s]
            den += base
    if den <= 0:
        return 1.0
    return min(1 + ALPHA_LIMIT, max(1 - ALPHA_LIMIT, num / den))


def forecast(hist: dict[int, float], first_slot: int, n: int, corrected: bool = True) -> list[float | None]:
    """预测 first_slot 起 n 个时段。corrected=False 为日前预测（只用前一日及以前的数据）。"""
    alpha = correction(hist, first_slot) if corrected else 1.0
    out: list[float | None] = []
    for h in range(n):
        base = similar_day(hist, first_slot + h)
        out.append(None if base is None else base * (1 + (alpha - 1) * math.exp(-h / TAU_SLOTS)))
    return out


def accuracy(actual: list[float | None], fcst: list[float | None]) -> dict | None:
    pairs = [
        (a, f) for a, f in zip(actual, fcst, strict=True) if a is not None and f is not None and abs(a) > 1e-3
    ]
    if len(pairs) < SLOTS_PER_DAY // 2:
        return None
    rel = [(a - f) / a for a, f in pairs]
    return {
        "accuracy": 100 * (1 - math.sqrt(sum(r * r for r in rel) / len(rel))),
        "mape": 100 * sum(abs(r) for r in rel) / len(rel),
        "max_err": max(abs(a - f) for a, f in pairs),
        "points": len(pairs),
    }
