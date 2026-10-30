import pytest

from rtusim.simulator import Simulator
from scadalib.iec104 import DPI
from scadalib.model import default_config_path, load_station


@pytest.fixture(scope="module")
def station():
    return load_station(default_config_path())


@pytest.fixture
def sim(station):
    s = Simulator(station, seed=42, start_hour=11.0)
    s.on_yx = None
    return s


def v(sim, pid):
    return sim.values[pid].v


def test_power_balance(sim):
    """两回进线受入有功 ≈ 各出线负荷 − 光伏出力 + 网损（网损 < 3%）。"""
    supply = v(sim, "L1.P") + v(sim, "L2.P")
    load = sum(v(sim, f"{f}.P") for f in ("F01", "F02", "F03", "F05")) - v(sim, "F04.P")
    assert supply > 0
    assert 0 < supply - load < 0.03 * supply


def test_voltage_within_limits(sim):
    for bus in ("BUS1", "BUS2"):
        assert 10.0 <= v(sim, f"{bus}.UAB") <= 10.7
    assert 106.7 <= v(sim, "L1.UAB") <= 117.7
    assert 49.9 < v(sim, "BUS1.F") < 50.1


async def test_breaker_trip_deenergizes_feeder(sim, monkeypatch):
    monkeypatch.setattr("rtusim.simulator.BREAKER_OP_S", 0)
    assert v(sim, "F02.P") > 0.5
    events = []
    sim.on_yx = lambda p, val, ts: events.append((p.id, val.v))
    assert await sim.operate_switch("912", close=False)
    sim.step(1.0)
    assert v(sim, "912.POS") == DPI.OFF
    assert v(sim, "F02.P") == 0 and v(sim, "F02.I") == 0
    assert events == [("912.POS", DPI.OFF)]


async def test_disconnector_travel_reports_intermediate_state(sim, monkeypatch):
    monkeypatch.setattr("rtusim.simulator.DISCONNECTOR_TRAVEL_S", 0)
    events = []
    sim.on_yx = lambda p, val, ts: events.append(val.v)
    await sim.operate_switch("101", close=False)
    await sim.operate_switch("1016", close=False)
    assert events == [DPI.OFF, DPI.INDETERMINATE, DPI.OFF]


def test_interlock_blocks_disconnector_under_load(sim):
    assert sim.interlock_reason("1016", close=False) is not None  # 101 合位
    assert sim.interlock_reason("1101", close=False) is None  # 主变高压侧无 110kV 断路器


async def test_tap_raise_increases_lv_voltage(sim, monkeypatch):
    monkeypatch.setattr("rtusim.simulator.OLTC_OP_S", 0)
    u0, pos0 = v(sim, "BUS1.UAB"), v(sim, "T1.TAP")
    assert sim.tap_reason("T1", higher=True) is None
    await sim.change_tap("T1", higher=True)
    sim.step(1.0)
    assert v(sim, "T1.TAP") == pos0 + 1
    assert v(sim, "BUS1.UAB") - u0 == pytest.approx(u0 * 0.0125, rel=0.3)  # 一档 1.25%


def test_tap_limits(sim):
    sim.tap["T1"] = -8  # 最高档（17 档）
    assert sim.tap_reason("T1", higher=True) == "已到极限档位"
    assert sim.tap_reason("T1", higher=False) is None


def test_clock_sync_corrects_offset(sim):
    from datetime import UTC, datetime

    assert abs(sim.clock_offset_s) > 0.5
    sim.clock_sync(datetime.now(UTC))
    assert abs(sim.clock_offset_s) < 0.05


def test_energy_counters_increase(sim):
    e0 = v(sim, "F01.E_IMP")
    sim.step(60.0)  # 60s × 60 倍速 = 1 小时
    assert v(sim, "F01.E_IMP") - e0 == pytest.approx(v(sim, "F01.P") * 1000, rel=0.15)
