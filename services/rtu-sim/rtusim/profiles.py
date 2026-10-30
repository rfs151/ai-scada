"""负荷与光伏出力曲线。

典型日负荷曲线（24 点，标幺值，峰值 = 1）线性插值，叠加 Ornstein-Uhlenbeck 随机波动，
避免白噪声带来的"毛刺感"，曲线更接近真实负荷的连续变化。
"""

import math
import random

PROFILES: dict[str, list[float]] = {
    # 工业：两班制，早 8 点爬坡，午休小谷，夜间保持基荷
    "industrial": [0.55, 0.52, 0.50, 0.50, 0.52, 0.60, 0.75, 0.90, 0.98, 1.00, 0.98, 0.85,
                   0.80, 0.95, 1.00, 0.98, 0.95, 0.88, 0.75, 0.70, 0.68, 0.65, 0.60, 0.57],
    # 居民：早高峰 + 晚高峰（19~20 点最大），凌晨低谷
    "residential": [0.45, 0.38, 0.35, 0.33, 0.34, 0.40, 0.55, 0.65, 0.60, 0.55, 0.55, 0.62,
                    0.60, 0.52, 0.50, 0.52, 0.60, 0.78, 0.95, 1.00, 0.98, 0.90, 0.75, 0.58],
    # 商业：营业时间（9~21 点）高平台
    "commercial": [0.30, 0.28, 0.27, 0.27, 0.28, 0.30, 0.38, 0.55, 0.80, 0.92, 0.97, 1.00,
                   0.95, 0.93, 0.97, 1.00, 0.98, 0.95, 0.92, 0.90, 0.85, 0.70, 0.50, 0.38],
}  # fmt: skip


def profile_value(kind: str, hour: float) -> float:
    """hour: 0~24 的小数小时。"""
    pts = PROFILES[kind]
    h = hour % 24
    i = int(h)
    frac = h - i
    return pts[i] * (1 - frac) + pts[(i + 1) % 24] * frac


def pv_clear_sky(hour: float, sunrise: float = 6.0, sunset: float = 18.5, peak: float = 0.85) -> float:
    """晴空出力标幺值（相对装机容量）：日出到日落的正弦半波，峰值 ≈ 0.85（组件温升与逆变器损耗）。"""
    h = hour % 24
    if not sunrise < h < sunset:
        return 0.0
    return peak * math.sin(math.pi * (h - sunrise) / (sunset - sunrise)) ** 1.3


class OU:
    """Ornstein-Uhlenbeck 过程：均值回复的随机波动。"""

    def __init__(self, mean: float = 0.0, theta: float = 0.05, sigma: float = 0.01, x0: float | None = None,
                 lo: float = -math.inf, hi: float = math.inf, rng: random.Random | None = None):  # fmt: skip
        self.mean, self.theta, self.sigma = mean, theta, sigma
        self.lo, self.hi = lo, hi
        self.x = mean if x0 is None else x0
        self.rng = rng or random.Random()

    def step(self, dt: float = 1.0) -> float:
        self.x += self.theta * (self.mean - self.x) * dt + self.sigma * math.sqrt(dt) * self.rng.gauss()
        self.x = min(self.hi, max(self.lo, self.x))
        return self.x
