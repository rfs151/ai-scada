import pytest

from app.alarm.rules import AlarmRules
from scadalib.iec104 import DPI, Quality
from scadalib.model import default_config_path, load_station


@pytest.fixture
def r():
    return AlarmRules(load_station(default_config_path()), limit_delay_ms=0)


def kinds(drafts):
    return [(d.level, d.kind) for d in drafts]


def test_limit_with_hysteresis(r):
    # 10kV 母线 Uab：上限 10.7、上上限 11.0，死区 0.02 → 回差 max(0.02, 10.7×1%) = 0.107
    r.init_values({"BUS1.UAB": (10.5, 0)})
    assert r.on_analog("BUS1.UAB", 10.6, 0, 1) == []
    a = r.on_analog("BUS1.UAB", 10.72, 0, 2)
    assert kinds(a) == [(3, "越限")] and "越上限" in a[0].text and "限值 10.7" in a[0].text
    # 在限值附近抖动：不重复告警，也不恢复
    assert r.on_analog("BUS1.UAB", 10.68, 0, 3) == []
    assert r.on_analog("BUS1.UAB", 10.71, 0, 4) == []
    # 越上上限升级
    assert "越上上限" in r.on_analog("BUS1.UAB", 11.05, 0, 5)[0].text
    # 回落到上限区间（需低于 11.0 - 0.11）
    assert r.on_analog("BUS1.UAB", 10.95, 0, 6) == []
    assert "回落至越上限" in r.on_analog("BUS1.UAB", 10.85, 0, 7)[0].text
    # 恢复正常（需低于 10.7 - 0.107）
    assert r.on_analog("BUS1.UAB", 10.62, 0, 8) == []
    assert kinds(r.on_analog("BUS1.UAB", 10.55, 0, 9)) == [(5, "越限恢复")]


def test_bad_quality_ignored(r):
    r.init_values({"BUS1.UAB": (10.5, 0)})
    assert r.on_analog("BUS1.UAB", 12.0, Quality.NT, 1) == []


def test_bus_dead_suppresses_frequency(r):
    r.init_values({"BUS1.UAB": (10.5, 0), "BUS1.F": (50.0, 0)})
    a = r.on_analog("BUS1.UAB", 0.0, 0, 1)
    assert kinds(a) == [(2, "失压")]
    assert r.on_analog("BUS1.F", 0.0, 0, 2) == []  # 失压闭锁频率告警
    assert r.on_analog("BUS1.UBC", 0.0, 0, 2) == []  # 只用 Uab 代表
    assert kinds(r.on_analog("BUS1.UAB", 10.45, 0, 3)) == [(5, "失压恢复")]


def test_switch_change_classification(r):
    ev = {"p": "912.POS", "v": DPI.OFF, "old": DPI.ON, "dev_t": 1000, "recv_t": 1003, "cot": 3}
    # 遥控
    a = r.on_yx(ev, {"action": "open", "operator": "张工", "supervisor": "王工"})
    assert kinds(a) == [(4, "遥控变位")] and "张工" in a[0].text and "王工" in a[0].text
    # 非遥控
    assert kinds(r.on_yx(ev)) == [(4, "变位")]
    # 保护动作后分闸 → 事故跳闸
    assert kinds(r.on_yx({"p": "F02.PROT", "v": 1, "old": 0, "dev_t": 980, "recv_t": 990, "cot": 3})) == [
        (1, "保护动作")
    ]
    a = r.on_yx(ev)
    assert kinds(a) == [(1, "事故跳闸")] and a[0].ts == 1000  # 用设备时标
    # 刀闸行程中间态不告警，不定态告警
    assert r.on_yx({**ev, "p": "1016.POS", "v": DPI.INDETERMINATE}) == []
    assert kinds(r.on_yx({**ev, "p": "1016.POS", "v": DPI.INVALID})) == [(2, "位置不定")]


def test_signals_and_gi(r):
    a = r.on_yx({"p": "F01.SPRING", "v": 1, "old": 0, "dev_t": None, "recv_t": 5, "cot": 20})
    assert kinds(a) == [(2, "告警信号")] and "总召发现" in a[0].text and a[0].ts == 5
    assert kinds(r.on_yx({"p": "F01.SPRING", "v": 0, "old": 1, "dev_t": 6, "recv_t": 7, "cot": 3})) == [
        (5, "复归")
    ]


def test_channel(r):
    assert r.on_channel("rtu1", "up", 1) == []  # 首次观测
    assert kinds(r.on_channel("rtu1", "down", 2, "connect failed")) == [(2, "通道中断")]
    assert r.on_channel("rtu1", "connecting", 3) == []
    assert kinds(r.on_channel("rtu1", "up", 4)) == [(5, "通道恢复")]


def test_limit_delay():
    r = AlarmRules(load_station(default_config_path()), limit_delay_ms=10_000)
    r.init_values({"BUS1.UAB": (10.5, 0)})
    # 短时越限后回落：不告警
    assert r.on_analog("BUS1.UAB", 10.72, 0, 0) == []
    assert r.on_analog("BUS1.UAB", 10.55, 0, 3_000) == []
    assert r.tick(20_000) == []
    # 持续越限 10 秒：确认告警，时标为开始越限的时刻
    assert r.on_analog("BUS1.UAB", 10.75, 0, 30_000) == []
    assert r.on_analog("BUS1.UAB", 10.76, 0, 35_000) == []
    a = r.tick(40_000)
    assert kinds(a) == [(3, "越限")] and a[0].ts == 30_000 and "10.76" in a[0].text
    # 越上上限立即告警
    assert "越上上限" in r.on_analog("BUS1.UAB", 11.05, 0, 41_000)[0].text
    # 恢复同样需持续
    assert r.on_analog("BUS1.UAB", 10.4, 0, 50_000) == []
    assert kinds(r.on_analog("BUS1.UAB", 10.45, 0, 60_000)) == [(5, "越限恢复")]
