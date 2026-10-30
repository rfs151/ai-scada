"""IEC 104 子站（被控站）。

规约行为:
    - 总召唤 C_IC_NA_1: 激活确认 → 全部遥信/遥测(COT=20) → 激活终止
    - 电度召唤 C_CI_NA_1: 激活确认 → 全部电度(COT=37) → 激活终止
    - 时钟同步 C_CS_NA_1: 校正站内时钟，激活确认
    - 遥控/遥调: 支持选择-执行(SBO)。选择 → 激活确认；执行 → 激活确认 → 动作完成后激活终止；
      选择后超时未执行自动撤销；停止激活(COT=8) 撤销选择
    - 未知类型/原因/公共地址/信息体地址 → 否定确认 COT 44/45/46/47
    - 数据变化通过 publish() 以突发(COT=3)方式上送给所有已启动的连接
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, tzinfo
from enum import Enum
from typing import Protocol

from . import cp56
from .asdu import Asdu, InfoObject, chunk
from .constants import COMMAND_TYPES, COT, TypeID
from .link import FrameHook, Link, LinkClosed, LinkParams

log = logging.getLogger(__name__)

SELECT_TIMEOUT_S = 30.0


class CmdCheck(Enum):
    OK = "ok"
    UNKNOWN_IOA = "unknown_ioa"
    REJECT = "reject"  # 闭锁、就地位置、状态不允许等


class ServerHandler(Protocol):
    def interrogation(self) -> dict[TypeID, list[InfoObject]]:
        """总召唤数据：按类型分组的全部遥信/遥测（不带时标的类型）。"""

    def counters(self) -> list[InfoObject]:
        """电度量（M_IT_NA_1）。"""

    def check_command(self, type_id: TypeID, obj: InfoObject) -> CmdCheck: ...

    def requires_select(self, type_id: TypeID, ioa: int) -> bool: ...

    async def execute_command(self, type_id: TypeID, obj: InfoObject) -> bool:
        """执行命令，动作完成后返回是否成功（决定激活终止的肯定/否定）。"""

    def clock_sync(self, master_time: datetime) -> None: ...


@dataclass(slots=True)
class _Selection:
    type_id: TypeID
    ioa: int
    value: object
    at: float


class Iec104Server:
    def __init__(
        self,
        handler: ServerHandler,
        *,
        ca: int = 1,
        host: str = "0.0.0.0",
        port: int = 2404,
        params: LinkParams = LinkParams(),
        tz: tzinfo = cp56.DEFAULT_TZ,
        on_frame: FrameHook | None = None,
        on_connection: Callable[[str, bool], None] | None = None,
        max_connections: int = 4,
    ):
        self.handler, self.ca, self.host, self.port = handler, ca, host, port
        self.params, self.tz = params, tz
        self._on_frame, self._on_connection = on_frame, on_connection
        self.max_connections = max_connections
        self.links: set[Link] = set()
        self._selections: dict[tuple[Link, int], _Selection] = {}
        self._server: asyncio.Server | None = None
        self._init_sent = False

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._accept, self.host, self.port)
        log.info("IEC104 server listening on %s:%s (CA=%s)", self.host, self.port, self.ca)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
        # Python 3.12 起 wait_closed() 会等待所有连接结束，须先关闭链路
        for link in list(self.links):
            link.close("server stopping")
        if self._server:
            await self._server.wait_closed()

    @property
    def bound_port(self) -> int:
        assert self._server
        return self._server.sockets[0].getsockname()[1]

    # ------------------------------------------------------------ 上送

    async def publish(self, type_id: TypeID, objects: list[InfoObject], cot: COT = COT.SPONTANEOUS) -> None:
        if not objects:
            return
        for link in [lk for lk in self.links if lk.started and not lk.closed]:
            try:
                for batch in chunk(type_id, objects):
                    await link.send_asdu(Asdu(type_id, cot, self.ca, batch))
            except LinkClosed:
                pass

    # ------------------------------------------------------------ 连接

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if len(self.links) >= self.max_connections:
            writer.close()
            return
        link = Link(
            reader,
            writer,
            on_asdu=self._on_asdu,
            params=self.params,
            tz=self.tz,
            on_frame=self._on_frame,
            on_started=self._on_started,
        )
        self.links.add(link)
        if self._on_connection:
            self._on_connection(link.name, True)
        try:
            await link.run()
        finally:
            self.links.discard(link)
            for key in [k for k in self._selections if k[0] is link]:
                del self._selections[key]
            if self._on_connection:
                self._on_connection(link.name, False)

    async def _on_started(self, link: Link) -> None:
        # 子站初始化后的第一次启动上送"初始化结束"
        if not self._init_sent:
            self._init_sent = True
            await link.send_asdu(Asdu(TypeID.M_EI_NA_1, COT.INITIALIZED, self.ca, [InfoObject(0, 0)]))

    # ------------------------------------------------------------ 处理主站报文

    async def _on_asdu(self, link: Link, a: Asdu | None, raw: bytes) -> None:
        if a is None:
            await self._reject_raw(link, raw)
            return
        if a.ca != self.ca and a.ca != 0xFFFF:
            await link.send_asdu(a.reply(COT.UNKNOWN_CA, negative=True))
            return
        match a.type_id:
            case TypeID.C_IC_NA_1:
                await self._interrogation(link, a)
            case TypeID.C_CI_NA_1:
                await self._counter_interrogation(link, a)
            case TypeID.C_CS_NA_1:
                await self._clock_sync(link, a)
            case t if t in COMMAND_TYPES:
                await self._command(link, a)
            case _:
                await link.send_asdu(a.reply(COT.UNKNOWN_TYPE, negative=True))

    async def _reject_raw(self, link: Link, raw: bytes) -> None:
        # 无法解析的类型：原样回送，COT 置 44(未知类型) + P/N 否定位
        if len(raw) >= 6:
            await link.send_raw_asdu(bytes([raw[0], raw[1], COT.UNKNOWN_TYPE | 0x40]) + raw[3:])

    async def _interrogation(self, link: Link, a: Asdu) -> None:
        if a.cot != COT.ACTIVATION:
            await link.send_asdu(a.reply(COT.UNKNOWN_CAUSE, negative=True))
            return
        await link.send_asdu(a.reply(COT.ACTIVATION_CON))
        for type_id, objs in self.handler.interrogation().items():
            for batch in chunk(type_id, objs):
                await link.send_asdu(Asdu(type_id, COT.INTERROGATED_BY_STATION, self.ca, batch))
        await link.send_asdu(a.reply(COT.ACTIVATION_TERM))

    async def _counter_interrogation(self, link: Link, a: Asdu) -> None:
        if a.cot != COT.ACTIVATION:
            await link.send_asdu(a.reply(COT.UNKNOWN_CAUSE, negative=True))
            return
        await link.send_asdu(a.reply(COT.ACTIVATION_CON))
        for batch in chunk(TypeID.M_IT_NA_1, self.handler.counters()):
            await link.send_asdu(Asdu(TypeID.M_IT_NA_1, COT.REQUESTED_BY_GENERAL_COUNTER, self.ca, batch))
        await link.send_asdu(a.reply(COT.ACTIVATION_TERM))

    async def _clock_sync(self, link: Link, a: Asdu) -> None:
        if a.cot != COT.ACTIVATION:
            await link.send_asdu(a.reply(COT.UNKNOWN_CAUSE, negative=True))
            return
        self.handler.clock_sync(a.objects[0].value)
        await link.send_asdu(a.reply(COT.ACTIVATION_CON))

    async def _command(self, link: Link, a: Asdu) -> None:
        obj = a.objects[0]
        key = (link, obj.ioa)

        if a.cot == COT.DEACTIVATION:  # 撤销选择
            self._selections.pop(key, None)
            await link.send_asdu(a.reply(COT.DEACTIVATION_CON))
            return
        if a.cot != COT.ACTIVATION:
            await link.send_asdu(a.reply(COT.UNKNOWN_CAUSE, negative=True))
            return

        check = self.handler.check_command(a.type_id, obj)
        if check is CmdCheck.UNKNOWN_IOA:
            await link.send_asdu(a.reply(COT.UNKNOWN_IOA, negative=True))
            return
        if check is CmdCheck.REJECT:
            await link.send_asdu(a.reply(COT.ACTIVATION_CON, negative=True))
            return

        if obj.select:
            self._selections[key] = _Selection(a.type_id, obj.ioa, obj.value, time.monotonic())
            await link.send_asdu(a.reply(COT.ACTIVATION_CON))
            return

        if self.handler.requires_select(a.type_id, obj.ioa):
            sel = self._selections.pop(key, None)
            valid = (
                sel is not None
                and sel.type_id == a.type_id
                and sel.value == obj.value
                and time.monotonic() - sel.at <= SELECT_TIMEOUT_S
            )
            if not valid:
                log.info(
                    "execute without valid select rejected: %s ioa=%s",
                    a.type_id.name,
                    obj.ioa,
                )
                await link.send_asdu(a.reply(COT.ACTIVATION_CON, negative=True))
                return

        await link.send_asdu(a.reply(COT.ACTIVATION_CON))
        ok = await self.handler.execute_command(a.type_id, obj)
        await link.send_asdu(a.reply(COT.ACTIVATION_TERM, negative=not ok))
