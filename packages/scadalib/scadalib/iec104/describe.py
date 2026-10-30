"""把 APDU 解析为可读文本，用于报文监视窗口。"""

from datetime import tzinfo

from . import apci, cp56
from .asdu import AsduError, InfoObject
from .asdu import decode as decode_asdu
from .constants import COT, DPI, Quality, TypeID

COT_CN = {
    COT.PERIODIC: "周期",
    COT.BACKGROUND: "背景扫描",
    COT.SPONTANEOUS: "突发",
    COT.INITIALIZED: "初始化",
    COT.REQUEST: "请求",
    COT.ACTIVATION: "激活",
    COT.ACTIVATION_CON: "激活确认",
    COT.DEACTIVATION: "停止激活",
    COT.DEACTIVATION_CON: "停止激活确认",
    COT.ACTIVATION_TERM: "激活终止",
    COT.INTERROGATED_BY_STATION: "响应总召",
    COT.REQUESTED_BY_GENERAL_COUNTER: "响应电度召唤",
    COT.UNKNOWN_TYPE: "未知类型",
    COT.UNKNOWN_CAUSE: "未知原因",
    COT.UNKNOWN_CA: "未知公共地址",
    COT.UNKNOWN_IOA: "未知信息体地址",
}

TYPE_CN = {
    TypeID.M_SP_NA_1: "单点遥信",
    TypeID.M_DP_NA_1: "双点遥信",
    TypeID.M_ME_NC_1: "遥测(浮点)",
    TypeID.M_IT_NA_1: "电度",
    TypeID.M_SP_TB_1: "单点SOE",
    TypeID.M_DP_TB_1: "双点SOE",
    TypeID.M_ME_TF_1: "遥测(浮点+时标)",
    TypeID.M_IT_TB_1: "电度(时标)",
    TypeID.M_EI_NA_1: "初始化结束",
    TypeID.C_SC_NA_1: "单点遥控",
    TypeID.C_DC_NA_1: "双点遥控",
    TypeID.C_RC_NA_1: "升降命令",
    TypeID.C_SE_NC_1: "设点(浮点)",
    TypeID.C_IC_NA_1: "总召唤",
    TypeID.C_CI_NA_1: "电度召唤",
    TypeID.C_CS_NA_1: "时钟同步",
}

U_CN = {
    apci.UFunc.STARTDT_ACT: "STARTDT 激活",
    apci.UFunc.STARTDT_CON: "STARTDT 确认",
    apci.UFunc.STOPDT_ACT: "STOPDT 激活",
    apci.UFunc.STOPDT_CON: "STOPDT 确认",
    apci.UFunc.TESTFR_ACT: "TESTFR 激活",
    apci.UFunc.TESTFR_CON: "TESTFR 确认",
}

MAX_OBJECTS_SHOWN = 6


def _q(q: Quality) -> str:
    flags = [f.name for f in (Quality.IV, Quality.NT, Quality.SB, Quality.BL, Quality.OV) if q & f]
    return f" [{','.join(flags)}]" if flags else ""


def _qoi(q: int) -> str:
    """召唤限定词 QOI：20 站召唤（总召），21~36 第 1~16 组召唤。"""
    if q == 20:
        return "QOI=20 总召"
    if 21 <= q <= 36:
        return f"QOI={q} 第{q - 20}组召唤"
    return f"QOI={q}"


_FRZ_CN = {0: "", 1: " 冻结不复位", 2: " 冻结带复位", 3: " 复位"}


def _qcc(q: int) -> str:
    """电度召唤限定词 QCC：低 6 位 RQT（5 总请求，1~4 第 1~4 组），高 2 位 FRZ（冻结/复位）。"""
    rqt, frz = q & 0x3F, q >> 6
    what = "总请求" if rqt == 5 else (f"第{rqt}组" if 1 <= rqt <= 4 else "")
    return f"QCC={q} {what}{_FRZ_CN[frz]}".rstrip()


def _obj(t: TypeID, o: InfoObject, tz: tzinfo) -> str:
    if t in (TypeID.C_IC_NA_1, TypeID.C_CI_NA_1):
        # 站级命令，标准规定 IOA=0；不为 0 时照实显示，便于排查对端实现问题
        text = _qoi(o.value) if t == TypeID.C_IC_NA_1 else _qcc(o.value)
        return text if o.ioa == 0 else f"IOA={o.ioa} {text}"
    v = o.value
    if isinstance(v, DPI):
        v = {
            DPI.OFF: "分",
            DPI.ON: "合",
            DPI.INDETERMINATE: "中间态",
            DPI.INVALID: "不定",
        }[v]
    elif isinstance(v, bool):
        v = "1" if v else "0"
    elif isinstance(v, float):
        v = f"{v:.3f}"
    elif t == TypeID.C_CS_NA_1:
        v = v.astimezone(tz).strftime("%H:%M:%S.%f")[:-3]
    s = f"{o.ioa}={v}{_q(o.quality)}"
    if t.name.startswith("C_") and t != TypeID.C_CS_NA_1:
        s += " 选择" if o.select else " 执行"
    if o.time:
        s += " @" + o.time.astimezone(tz).strftime("%H:%M:%S.%f")[:-3]
    return s


def describe(raw: bytes, tz: tzinfo = cp56.DEFAULT_TZ) -> str:
    try:
        f = apci.decode(raw)
    except apci.FrameError as e:
        return f"格式错误: {e}"
    if f.kind is apci.Kind.U:
        return f"U  {U_CN.get(f.ufunc, '?')}"
    if f.kind is apci.Kind.S:
        return f"S  N(R)={f.nr}"
    head = f"I  N(S)={f.ns} N(R)={f.nr}  "
    try:
        a = decode_asdu(f.asdu, tz)
    except AsduError as e:
        return head + f"ASDU 无法解析: {e}"
    cause = COT_CN.get(a.cot, str(a.cot))
    if a.negative:
        cause += "(否定)"
    objs = ", ".join(_obj(a.type_id, o, tz) for o in a.objects[:MAX_OBJECTS_SHOWN])
    more = f" …共{len(a.objects)}个" if len(a.objects) > MAX_OBJECTS_SHOWN else ""
    return f"{head}{a.type_id.name} {TYPE_CN.get(a.type_id, '')} {cause} CA={a.ca}  {objs}{more}"
