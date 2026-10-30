"""相似日负荷预测。"""

import math
import random

from app.forecast.model import SLOTS_PER_DAY, accuracy, correction, forecast, similar_day

D = SLOTS_PER_DAY


def shape(s: int) -> float:
    return 10 + 4 * math.sin(2 * math.pi * (s % D) / D)


def history(days: int, noise: float = 0.0, seed: int = 1) -> dict[int, float]:
    rng = random.Random(seed)
    return {s: shape(s) * (1 + rng.gauss(0, noise)) for s in range(days * D)}


def test_repeating_profile_is_reproduced():
    h = history(8)
    fc = forecast(h, 8 * D, D)
    assert max(abs(f - shape(8 * D + i)) for i, f in enumerate(fc)) < 1e-9
    assert accuracy([shape(8 * D + i) for i in range(D)], fc)["accuracy"] > 99.99


def test_outlier_day_is_rejected():
    h = history(8)
    for s in range(7 * D, 8 * D):  # 前一日事故停电，负荷只有一半
        h[s] *= 0.5
    assert abs(similar_day(h, 8 * D + 10) - shape(10)) < 1e-9


def test_realtime_correction_decays():
    h = history(8)
    for s in range(8 * D - 4, 8 * D):  # 最近 1 小时实测比历史高 10%
        h[s] *= 1.1
    assert abs(correction(h, 8 * D) - 1.1) < 1e-9
    fc = forecast(h, 8 * D, D)
    assert abs(fc[0] / shape(0) - 1.1) < 1e-9
    assert abs(fc[-1] / similar_day(h, 9 * D - 1) - 1) < 1e-3  # 远期回到相似日基准


def test_accuracy_metric():
    m = accuracy([10.0] * D, [9.0] * D)
    assert abs(m["accuracy"] - 90) < 1e-9 and abs(m["mape"] - 10) < 1e-9 and abs(m["max_err"] - 1) < 1e-9
    assert accuracy([None] * D, [1.0] * D) is None


def test_noisy_history_accuracy():
    h = history(9, noise=0.02)
    actual = [h[s] for s in range(8 * D, 9 * D)]
    m = accuracy(actual, forecast(h, 8 * D, D, corrected=False))
    assert 95 < m["accuracy"] < 99.5


def test_correction_is_limited():
    h = history(8)
    for s in range(8 * D - 4, 8 * D):  # 坏数据：最近 1 小时只剩一半
        h[s] *= 0.5
    assert abs(correction(h, 8 * D) - 0.85) < 1e-9
