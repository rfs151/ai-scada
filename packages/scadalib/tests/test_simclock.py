from datetime import datetime
from zoneinfo import ZoneInfo

from scadalib.simclock import day_seconds, day_start, sim_hour

SH = ZoneInfo("Asia/Shanghai")


def ts(h, m, s=0):
    return datetime(2026, 10, 30, h, m, s, tzinfo=SH).timestamp()


def test_sim_day_aligned_to_local_clock():
    assert day_seconds() == 1440
    assert sim_hour(ts(22, 0)) == 0 and sim_hour(ts(22, 24)) == 0 and sim_hour(ts(22, 48)) == 0
    assert sim_hour(ts(22, 12)) == 12  # 12 分钟 = 半个仿真日
    assert abs(sim_hour(ts(22, 30, 15)) - 6.25) < 1e-9
    assert day_start(ts(22, 30, 15)) == ts(22, 24)


def test_other_speed():
    assert day_seconds(1) == 86_400
    assert abs(sim_hour(ts(13, 30), speed=1) - 13.5) < 1e-9  # 1 倍速即本地钟点
