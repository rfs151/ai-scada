"""IEC 60870-5-101/104 常量：类型标识、传送原因、品质描述、信息体地址分段。"""

from enum import IntEnum, IntFlag


class TypeID(IntEnum):
    # ---- 监视方向（子站 → 主站）----
    M_SP_NA_1 = 1  # 单点遥信
    M_DP_NA_1 = 3  # 双点遥信
    M_ME_NC_1 = 13  # 遥测，短浮点
    M_IT_NA_1 = 15  # 累计量（电度）
    M_SP_TB_1 = 30  # 单点遥信，带 CP56Time2a（SOE）
    M_DP_TB_1 = 31  # 双点遥信，带 CP56Time2a（SOE）
    M_ME_TF_1 = 36  # 遥测短浮点，带 CP56Time2a
    M_IT_TB_1 = 37  # 累计量，带 CP56Time2a
    M_EI_NA_1 = 70  # 初始化结束
    # ---- 控制方向（主站 → 子站）----
    C_SC_NA_1 = 45  # 单点遥控
    C_DC_NA_1 = 46  # 双点遥控
    C_RC_NA_1 = 47  # 升降命令（调档）
    C_SE_NC_1 = 50  # 设点命令，短浮点（遥调）
    C_IC_NA_1 = 100  # 总召唤
    C_CI_NA_1 = 101  # 电度量召唤
    C_CS_NA_1 = 103  # 时钟同步


class COT(IntEnum):
    """传送原因 Cause of Transmission。"""

    PERIODIC = 1  # 周期/循环
    BACKGROUND = 2  # 背景扫描
    SPONTANEOUS = 3  # 突发（自发）
    INITIALIZED = 4  # 初始化
    REQUEST = 5  # 请求
    ACTIVATION = 6  # 激活
    ACTIVATION_CON = 7  # 激活确认
    DEACTIVATION = 8  # 停止激活
    DEACTIVATION_CON = 9  # 停止激活确认
    ACTIVATION_TERM = 10  # 激活终止
    INTERROGATED_BY_STATION = 20  # 响应站总召唤
    REQUESTED_BY_GENERAL_COUNTER = 37  # 响应电度总召唤
    UNKNOWN_TYPE = 44
    UNKNOWN_CAUSE = 45
    UNKNOWN_CA = 46
    UNKNOWN_IOA = 47


class Quality(IntFlag):
    """品质描述词（QDS/SIQ/DIQ 的高位部分，各类型共用位定义）。"""

    GOOD = 0
    OV = 0x01  # 溢出（仅遥测 QDS）
    BL = 0x10  # 被闭锁
    SB = 0x20  # 被取代（人工置数）
    NT = 0x40  # 非当前值（未刷新）
    IV = 0x80  # 无效


class DPI(IntEnum):
    """双点信息：断路器/刀闸位置。00 和 11 为不确定态。"""

    INDETERMINATE = 0  # 中间态（分合过程中）
    OFF = 1  # 分
    ON = 2  # 合
    INVALID = 3  # 不定态（辅助接点异常）


class RCS(IntEnum):
    LOWER = 1  # 降
    HIGHER = 2  # 升


QOI_STATION = 20  # 总召唤限定词：站召唤
QCC_GENERAL = 5  # 电度召唤限定词：总的请求计数量


class IOA:
    """国内 104 规约实施细则常用的信息体地址分段。"""

    YX_START = 0x0001  # 遥信
    YC_START = 0x4001  # 遥测
    YK_START = 0x6001  # 遥控
    YT_START = 0x6201  # 遥调/设点
    DD_START = 0x6401  # 电度


# 监视方向哪些类型带时标
TIMED_TYPES = {TypeID.M_SP_TB_1, TypeID.M_DP_TB_1, TypeID.M_ME_TF_1, TypeID.M_IT_TB_1}
COMMAND_TYPES = {TypeID.C_SC_NA_1, TypeID.C_DC_NA_1, TypeID.C_RC_NA_1, TypeID.C_SE_NC_1}
