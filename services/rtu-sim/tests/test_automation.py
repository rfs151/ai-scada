"""自动装置：保护、重合闸、后加速、主变保护、两级备自投配合、单相接地。"""

import asyncio
import contextlib

import pytest

from rtusim import automation
from rtusim.simulator import Simulator
from scadalib.iec104 import DPI
from scadalib.model import default_config_path, load_station


@pytest.fixture(scope="module")
def station():
    return load_station(default_config_path())


@pytest.fixture
def sim(station, monkeypatch):
    # 缩短整定时间，保持各时限的相对大小（备自投 110kV < 10kV）
    for k, v in {"OVERCURRENT_S": 0.03, "ACCEL_S": 0.005, "RECLOSE_S": 0.05, "DIFF_S": 0.005,
                 "BZT_CHARGE_S": 0.2, "MONITOR_S": 0.01}.items():  # fmt: skip
        monkeypatch.setattr(automation, k, v)
    monkeypatch.setattr("rtusim.simulator.BREAKER_OP_S", 0)
    s = Simulator(station, seed=1, start_hour=11.0)
    s.auto.bzts["B100"].delay_s = 0.15
    s.auto.bzts["B900"].delay_s = 0.4
    return s


@contextlib.asynccontextmanager
async def running(sim):
    """替代 RTU 主循环：设备变化即重算潮流，并运行自动装置监视。"""
    events: list[tuple[str, object]] = []
    sim.on_yx = lambda p, v, ts: events.append((p.id, v.v))

    async def stepper():
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(sim.changed.wait(), 0.02)
            sim.changed.clear()
            sim.step(0.0)

    tasks = [asyncio.create_task(stepper()), asyncio.create_task(sim.auto.run())]
    try:
        yield events
    finally:
        for t in tasks:
            t.cancel()


async def until(cond, timeout=5.0):
    for _ in range(int(timeout / 0.02)):
        if cond():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met")


def seq(events, *ids):
    return [(p, v) for p, v in events if p in ids]


async def test_transient_fault_reclose_success(sim):
    async with running(sim) as ev:
        sim.auto.inject_feeder_fault("F02", "short", permanent=False)
        await until(lambda: ("F02.RECL", True) in ev and sim.switch["912"] == DPI.ON)
        await asyncio.sleep(0.2)
    assert seq(ev, "F02.PROT", "912.POS", "F02.RECL")[:4] == [
        ("F02.PROT", True), ("912.POS", DPI.OFF), ("F02.RECL", True), ("912.POS", DPI.ON),
    ]  # fmt: skip
    assert ("STN.ACC", True) in ev
    assert "F02" not in sim.auto.faults and sim.switch["912"] == DPI.ON


async def test_permanent_fault_accelerated_trip(sim):
    async with running(sim) as ev:
        sim.auto.inject_feeder_fault("F02", "short", permanent=True)
        await until(lambda: [x for x in ev if x == ("912.POS", DPI.OFF)].__len__() == 2)
        await asyncio.sleep(0.3)
        # 重合于永久故障 → 后加速跳闸，不再重合
        assert [v for p, v in seq(ev, "912.POS")] == [DPI.OFF, DPI.ON, DPI.OFF]
        assert sim.values["F02.I"].v == 0
        # 故障未消除时遥控合闸：后加速再次跳开
        ev.clear()
        await sim.operate_switch("912", True, source="remote")
        await until(lambda: sim.switch["912"] == DPI.OFF)
        await asyncio.sleep(0.2)
        assert ("F02.RECL", True) not in ev
        # 抢修完成后合闸成功
        sim.auto.clear_faults()
        await sim.operate_switch("912", True, source="remote")
        await asyncio.sleep(0.2)
        assert sim.switch["912"] == DPI.ON


async def test_line_loss_bridge_bzt_and_time_grading(sim):
    async with running(sim) as ev:
        await until(lambda: all(b.charged(automation.time.monotonic()) for b in sim.auto.bzts.values()))
        sim.auto.lose_line("L1")
        await until(lambda: sim.switch["100"] == DPI.ON)
        await asyncio.sleep(0.6)  # 超过 10kV 备自投延时
    assert ("B100.BZT", True) in ev and sim.switch["101"] == DPI.OFF
    assert ("B900.BZT", True) not in ev and sim.switch["900"] == DPI.OFF  # 时间级差：10kV 不动作
    assert sim.values["BUS1.UAB"].v > 9.5


async def test_trafo_fault_blocks_bridge_bzt_10kv_bzt_acts(sim):
    async with running(sim) as ev:
        await until(lambda: all(b.charged(automation.time.monotonic()) for b in sim.auto.bzts.values()))
        sim.auto.inject_trafo_fault("T1")
        await until(lambda: sim.switch["900"] == DPI.ON)
        await asyncio.sleep(0.1)
    assert ("T1.DIFF", True) in ev
    assert sim.switch["901"] == DPI.OFF and sim.switch["101"] == DPI.OFF
    assert sim.switch["100"] == DPI.OFF and ("B100.BZT", True) not in ev  # 主变保护闭锁桥备自投
    assert ("B900.BZT", True) in ev
    assert sim.values["BUS1.UAB"].v > 9.5


async def test_manual_open_discharges_bzt(sim):
    async with running(sim) as ev:
        await until(lambda: sim.auto.bzts["B900"].charged(automation.time.monotonic()))
        await sim.operate_switch("901", False, source="remote")
        await asyncio.sleep(0.7)
    assert ("B900.BZT", True) not in ev and sim.switch["900"] == DPI.OFF


async def test_single_phase_ground_and_line_selection(sim):
    async with running(sim) as ev:
        sim.auto.inject_feeder_fault("F03", "ground", permanent=True)
        await until(lambda: sim.signals["BUS2.GND"])
        await asyncio.sleep(0.1)
        assert sim.values["BUS2.U0"].v > 80 and sim.values["BUS1.U0"].v < 10
        assert sim.switch["921"] == DPI.ON  # 小电流接地不跳闸
        # 拉路选线：拉开故障线路，接地消失
        await sim.operate_switch("921", False, source="remote")
        await until(lambda: not sim.signals["BUS2.GND"])
    assert ("BUS2.GND", False) in ev


async def test_reset_normal(sim):
    async with running(sim):
        sim.auto.inject_feeder_fault("F01", "short", permanent=True)
        await until(lambda: sim.switch["911"] == DPI.OFF and not sim.auto.feeders["F01"].busy, timeout=8)
        await asyncio.sleep(0.3)
        sim.reset_normal()
        await asyncio.sleep(0.2)
    assert sim.switch["911"] == DPI.ON and not sim.auto.faults and not any(sim.signals.values())
