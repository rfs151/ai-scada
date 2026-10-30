from datetime import UTC, datetime

import pytest

from fep.processor import Processor, command_value
from scadalib.iec104 import COT, DPI, RCS, Asdu, InfoObject, Quality, TypeID
from scadalib.model import default_config_path, load_station


@pytest.fixture(scope="module")
def st():
    return load_station(default_config_path())


def gi(st, pid, value, type_id):
    return Asdu(type_id, COT.INTERROGATED_BY_STATION, 1, [InfoObject(st.point(pid).ioa, value)])


def test_analog_change_detection(st):
    proc = Processor(st)
    b = proc.process(gi(st, "F01.P", 5.0, TypeID.M_ME_NC_1), recv_t=1000)
    assert b.changes["F01.P"].v == 5.0 and b.changes["F01.P"].t == 1000
    # 总召唤刷新相同值：刷新实时值，但不产生变化记录
    b = proc.process(gi(st, "F01.P", 5.0, TypeID.M_ME_NC_1), recv_t=2000)
    assert "F01.P" in b.values and not b.changes
    b = proc.process(gi(st, "F01.P", 5.2, TypeID.M_ME_NC_1))
    assert b.changes["F01.P"].v == 5.2


def test_soe_uses_device_timestamp(st):
    proc = Processor(st)
    proc.process(gi(st, "912.POS", DPI.ON, TypeID.M_DP_NA_1))
    dev = datetime(2026, 10, 30, 3, 0, 0, 123000, tzinfo=UTC)
    a = Asdu(TypeID.M_DP_TB_1, COT.SPONTANEOUS, 1, [InfoObject(st.point("912.POS").ioa, DPI.OFF, time=dev)])
    b = proc.process(a, recv_t=9_999_999_999_999)
    (e,) = b.yx_events
    assert (e.point, e.value, e.old) == ("912.POS", DPI.OFF, DPI.ON)
    assert e.dev_t == int(dev.timestamp() * 1000)
    assert b.changes["912.POS"].t == e.dev_t  # 实时值时标也取设备时标


def test_gi_discovers_state_mismatch(st):
    proc = Processor(st)
    proc.process(gi(st, "101.POS", DPI.ON, TypeID.M_DP_NA_1))
    b = proc.process(gi(st, "101.POS", DPI.OFF, TypeID.M_DP_NA_1))
    (e,) = b.yx_events
    assert e.dev_t is None and e.cot == COT.INTERROGATED_BY_STATION


def test_unknown_or_mismatched_ioa_counted(st):
    proc = Processor(st)
    # 0x4FFF 不在点表；遥信 IOA 用遥测类型上送 → 类型不匹配
    a = Asdu(TypeID.M_ME_NC_1, COT.SPONTANEOUS, 1, [InfoObject(0x4FFF, 1.0), InfoObject(1, 1.0)])
    assert proc.process(a).unknown == 2


def test_channel_down_marks_not_topical(st):
    proc = Processor(st)
    proc.process(gi(st, "F01.P", 5.0, TypeID.M_ME_NC_1))
    b = proc.mark_not_topical()
    assert b.changes["F01.P"].q & Quality.NT and b.changes["F01.P"].v == 5.0
    assert not proc.mark_not_topical().changes  # 已是 NT 不重复记录
    # 恢复后总召唤，品质恢复 → 产生变化
    assert proc.process(gi(st, "F01.P", 5.0, TypeID.M_ME_NC_1)).changes["F01.P"].q == 0


def test_command_value():
    assert command_value(TypeID.C_DC_NA_1, "close") is DPI.ON
    assert command_value(TypeID.C_DC_NA_1, "open") is DPI.OFF
    assert command_value(TypeID.C_RC_NA_1, "raise") is RCS.HIGHER
    assert command_value(TypeID.C_SE_NC_1, 3) == 3.0
    for bad in [(TypeID.C_DC_NA_1, 1), (TypeID.C_SE_NC_1, True), (TypeID.C_RC_NA_1, "up")]:
        with pytest.raises(ValueError):
            command_value(*bad)
