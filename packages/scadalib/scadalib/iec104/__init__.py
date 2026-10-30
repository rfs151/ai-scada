"""自研 IEC 60870-5-104 规约栈（纯 Python asyncio，无第三方依赖）。

apci    帧层：I/S/U 帧编解码与 TCP 流切帧
asdu    应用层：类型标识编解码
cp56    CP56Time2a 时标
link    链路状态机：k/w 窗口、t1/t2/t3 定时器、STARTDT/TESTFR
server  子站：总召、电度召唤、对时、SBO 遥控、突发上送
client  主站：自动重连、周期召唤、命令确认等待
describe 报文解析为可读文本（报文监视）
"""

from .asdu import Asdu, InfoObject
from .client import CommandResult, Cycles, Iec104Client
from .constants import COT, DPI, IOA, RCS, Quality, TypeID
from .link import Link, LinkParams
from .server import CmdCheck, Iec104Server

__all__ = [
    "COT",
    "DPI",
    "IOA",
    "RCS",
    "Asdu",
    "CmdCheck",
    "CommandResult",
    "Cycles",
    "Iec104Client",
    "Iec104Server",
    "InfoObject",
    "Link",
    "LinkParams",
    "Quality",
    "TypeID",
]
