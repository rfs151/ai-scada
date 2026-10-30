"""拓扑分析：用真实厂站模型（config/station.yaml）验证典型运行方式。"""

import pytest

from scadalib.model import NodeState, analyze_station, default_config_path, load_station


@pytest.fixture(scope="module")
def st():
    return load_station(default_config_path())


def run(st, overrides=None, grounds=()):
    overrides = overrides or {}
    normal = {e.id: e.normally_closed for e in st.equipment if e.is_switch}
    return analyze_station(st, lambda eid: overrides.get(eid, normal.get(eid, False)), grounds)


def test_normal_mode_all_energized(st):
    t = run(st)
    for n in ("L1_T", "HB1", "T1_L", "BUS1", "BUS2", "F01_LD", "F04_PV"):
        assert t.state(n) is NodeState.ENERGIZED
    assert t.state("C1_N") is NodeState.DEAD  # 电容器 913 正常热备用
    assert not t.grounded


def test_bridge_transfer(st):
    assert run(st, {"101": False}).state("BUS1") is NodeState.DEAD
    t = run(st, {"101": False, "100": True})
    assert t.state("BUS1") is NodeState.ENERGIZED
    assert t.state("L1_T") is NodeState.ENERGIZED  # 线路由对侧充电


def test_earth_switch_and_ground_wire(st):
    t = run(st, {"911": False, "9117": True})
    assert t.state("F01_LD") is NodeState.GROUNDED and t.state("BUS1") is NodeState.ENERGIZED
    # 带地线合闸 → 冲突
    assert "BUS1" in run(st, {"9117": True}).conflicts
    # 临时接地线同样参与拓扑
    assert run(st, {"912": False}, grounds=["F02_LD"]).state("F02_O") is NodeState.GROUNDED


def test_ground_does_not_cross_transformer(st):
    t = run(st, {"101": False, "102": False, "911": True, "9117": True})
    assert t.state("T1_L") is NodeState.GROUNDED
    assert t.state("T1_H") is NodeState.DEAD
