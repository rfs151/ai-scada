"""遥测数据校核：正常断面无告警；三类量测故障能被发现并定位到间隔。"""

import json
from pathlib import Path

import pytest

from app.alarm.datacheck import PERSIST_MS, DataCheck
from scadalib.model import default_config_path, load_station

SNAP = json.loads((Path(__file__).parent / "fixtures" / "snapshot_normal.json").read_text())


@pytest.fixture
def dc():
    return DataCheck(load_station(default_config_path()))


def vals(**over):
    v = {k: (x, 0) for k, x in SNAP.items()}
    for k, x in over.items():
        v[k.replace("_", ".")] = (x, 0)
    return v


def keys(issues):
    return {i.key for i in issues}


def test_normal_snapshot_is_consistent(dc):
    assert dc.evaluate(vals(), 0) == []
    assert all(b["ok"] for b in dc.snapshot["balances"])


def test_ct_polarity_reversed(dc):
    issues = dc.evaluate(vals(F03_P=-SNAP["F03.P"], F03_Q=-SNAP["F03.Q"]), 0)
    assert {"sign:F03", "bal:BUS2"} <= keys(issues)
    bal = next(i for i in issues if i.key == "bal:BUS2")
    assert "商业中心线" in bal.text and bal.bay == "F03"


def test_frozen_transducer(dc):
    # 有功卡在旧值，负荷已变：P 与 U·I 不自洽，母线不平衡
    issues = dc.evaluate(vals(F01_P=SNAP["F01.P"] * 0.5), 0)
    assert {"bay:F01", "bal:BUS1"} <= keys(issues)
    assert next(i for i in issues if i.key == "bal:BUS1").bay == "F01"


def test_ct_ratio_error_only_breaks_bay_consistency(dc):
    # 电流量程错误：功率平衡不受影响，只有间隔自洽能发现
    issues = dc.evaluate(vals(F05_I=SNAP["F05.I"] * 1.25), 0)
    assert keys(issues) == {"bay:F05"}


def test_open_breaker_and_bad_quality_are_skipped(dc):
    v = vals(F01_P=0.0)
    v["911.POS"] = (1, 0)  # 分位：不参与自洽，也不计入母线
    issues = dc.evaluate(v, 0)
    assert "bay:F01" not in keys(issues)
    v = vals(F01_P=SNAP["F01.P"] * 0.5)
    v["F01.P"] = (v["F01.P"][0], 0x80)  # IV 无效
    assert "bal:BUS1" not in keys(dc.evaluate(v, 0))


def test_bus_tie_closed_merges_buses(dc):
    v = vals()
    v["900.POS"] = (2, 0)
    dc.evaluate(v, 0)
    assert [b["key"] for b in dc.snapshot["balances"]][0] == "bal:BUS1+BUS2"


def test_persistence_and_recovery(dc):
    bad = vals(F05_I=SNAP["F05.I"] * 1.25)
    assert dc.run(bad, 0) == []
    assert dc.run(bad, PERSIST_MS - 1000) == []  # 暂态不告警
    [a] = dc.run(bad, PERSIST_MS)
    assert a.kind == "数据校核" and a.bay == "F05" and a.level == 2
    assert dc.run(vals(), PERSIST_MS + 1000) == []
    [r] = dc.run(vals(), 2 * PERSIST_MS + 1000)
    assert r.kind == "数据校核恢复" and r.level == 5
    # 短暂异常（如短路电流）消失后不留痕
    dc.run(bad, 100_000)
    dc.run(vals(), 101_000)
    assert dc.tracks == {}
