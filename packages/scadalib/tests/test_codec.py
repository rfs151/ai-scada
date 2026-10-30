from datetime import UTC, datetime

import pytest

from scadalib.iec104 import apci, cp56
from scadalib.iec104 import asdu as A
from scadalib.iec104.constants import COT, DPI, RCS, Quality, TypeID
from scadalib.iec104.describe import describe

T = datetime(2026, 10, 30, 3, 8, 58, 728000, tzinfo=UTC)


def roundtrip(a: A.Asdu) -> A.Asdu:
    return A.decode(A.encode(a))


@pytest.mark.parametrize(
    ("type_id", "obj"),
    [
        (TypeID.M_SP_NA_1, A.InfoObject(1, True, Quality.NT)),
        (TypeID.M_DP_NA_1, A.InfoObject(2, DPI.ON, Quality.IV)),
        (TypeID.M_ME_NC_1, A.InfoObject(0x4001, 115.25, Quality.OV)),
        (TypeID.M_IT_NA_1, A.InfoObject(0x6401, 987654, qualifier=7)),
        (TypeID.M_SP_TB_1, A.InfoObject(3, False, time=T)),
        (TypeID.M_DP_TB_1, A.InfoObject(4, DPI.OFF, time=T)),
        (TypeID.M_ME_TF_1, A.InfoObject(0x4002, -1.5, time=T)),
        (TypeID.C_SC_NA_1, A.InfoObject(0x6001, True, select=True, qualifier=1)),
        (TypeID.C_DC_NA_1, A.InfoObject(0x6002, DPI.OFF, select=False)),
        (TypeID.C_RC_NA_1, A.InfoObject(0x6201, RCS.HIGHER, select=True)),
        (TypeID.C_SE_NC_1, A.InfoObject(0x6202, 42.5, select=False)),
        (TypeID.C_IC_NA_1, A.InfoObject(0, 20)),
        (TypeID.C_CS_NA_1, A.InfoObject(0, T)),
    ],
)
def test_roundtrip_all_types(type_id, obj):
    back = roundtrip(A.Asdu(type_id, COT.ACTIVATION, 1, [obj]))
    o = back.objects[0]
    assert back.type_id == type_id and o.ioa == obj.ioa
    assert o.value == obj.value
    assert o.quality == obj.quality
    assert o.select == obj.select
    assert o.time == obj.time


def test_header_flags():
    a = A.Asdu(
        TypeID.C_DC_NA_1,
        COT.ACTIVATION_CON,
        0x1234,
        [A.InfoObject(1, DPI.ON)],
        negative=True,
        oa=7,
    )
    raw = A.encode(a)
    assert raw[2] == 0x47  # COT 7 | P/N
    assert raw[3] == 7 and raw[4:6] == b"\x34\x12"
    back = A.decode(raw)
    assert back.negative and back.oa == 7 and back.ca == 0x1234


def test_decode_sequence_sq1():
    # SQ=1：3 个连续遥测，只有首个带 IOA 0x4001
    raw = bytes([13, 0x83, 20, 0, 1, 0]) + (0x4001).to_bytes(3, "little")
    raw += b"\x00\x00\x80\x3f\x00" + b"\x00\x00\x00\x40\x00" + b"\x00\x00\x40\x40\x00"
    a = A.decode(raw)
    assert [o.ioa for o in a.objects] == [0x4001, 0x4002, 0x4003]
    assert [o.value for o in a.objects] == [1.0, 2.0, 3.0]


def test_decode_errors():
    with pytest.raises(A.UnknownTypeError):
        A.decode(bytes([99, 1, 6, 0, 1, 0, 0, 0, 0, 0]))
    with pytest.raises(A.AsduError, match="truncated"):
        A.decode(bytes([13, 1, 3, 0, 1, 0, 1, 0x40, 0, 0, 0]))
    with pytest.raises(A.AsduError, match="timestamp"):
        A.encode(A.Asdu(TypeID.M_SP_TB_1, COT.SPONTANEOUS, 1, [A.InfoObject(1, True)]))


def test_chunk_respects_max_length():
    objs = [A.InfoObject(0x4001 + i, float(i)) for i in range(200)]
    batches = A.chunk(TypeID.M_ME_NC_1, objs)
    assert sum(len(b) for b in batches) == 200
    for b in batches:
        assert len(A.encode(A.Asdu(TypeID.M_ME_NC_1, COT.INTERROGATED_BY_STATION, 1, b))) <= A.ASDU_MAX_LEN


def test_cp56_is_beijing_local_time():
    raw = cp56.encode(T)  # 03:08:58.728 UTC = 11:08:58.728 北京时间
    assert raw[0:2] == (58728).to_bytes(2, "little")
    assert raw[2] == 8 and raw[3] == 11
    assert raw[4] & 0x1F == 30 and raw[4] >> 5 == 5  # 30 日，周五
    assert raw[5] == 10 and raw[6] == 26
    assert cp56.decode(raw) == (T, False)


def test_apci_frames_and_sequence_wrap():
    i = apci.decode(apci.encode_i(32767, 1, b"\x64\x01\x06\x00\x01\x00\x00\x00\x00\x14"))
    assert i.kind is apci.Kind.I and i.ns == 32767 and i.nr == 1
    assert apci.decode(apci.encode_i(32768, 0, b"x" * 6)).ns == 0
    assert apci.decode(apci.encode_s(300)).nr == 300
    assert apci.decode(apci.encode_u(apci.UFunc.TESTFR_ACT)).ufunc is apci.UFunc.TESTFR_ACT
    assert apci.seq_distance(32766, 2) == 4
    with pytest.raises(apci.FrameError):
        apci.decode(b"\x68\x04\x07\x00\x00")


def test_describe():
    raw = apci.encode_i(
        3,
        4,
        A.encode(A.Asdu(TypeID.M_DP_TB_1, COT.SPONTANEOUS, 1, [A.InfoObject(5, DPI.OFF, time=T)])),
    )
    text = describe(raw)
    assert "M_DP_TB_1" in text and "突发" in text and "5=分" in text and "11:08:58.728" in text
    assert describe(apci.encode_u(apci.UFunc.STARTDT_ACT)) == "U  STARTDT 激活"
