"""IEC 104 主站（控制站）。

连接建立后的标准流程:
    STARTDT → 时钟同步 → 总召唤 → 电度召唤；之后按周期重复总召/对时/电度召唤。
连接中断后按退避间隔自动重连。命令接口等待激活确认/激活终止并返回结果。
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from typing import Any

from . import cp56
from .asdu import Asdu, InfoObject
from .constants import COMMAND_TYPES, COT, QCC_GENERAL, QOI_STATION, TypeID
from .link import FrameHook, Link, LinkClosed, LinkParams

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Cycles:
    gi_s: float = 900.0  # 总召唤周期 15 min
    clock_s: float = 600.0  # 对时周期 10 min
    counters_s: float = 300.0  # 电度召唤周期 5 min


@dataclass(slots=True)
class CommandResult:
    ok: bool
    stage: str  # "actcon" / "actterm" / "timeout" / "link"
    detail: str = ""


class Iec104Client:
    def __init__(
        self,
        host: str,
        port: int = 2404,
        *,
        ca: int = 1,
        on_data: Callable[[Asdu], Awaitable[None]],
        on_state: Callable[[str, str], Awaitable[None]] | None = None,
        on_frame: FrameHook | None = None,
        params: LinkParams = LinkParams(),
        cycles: Cycles = Cycles(),
        tz: tzinfo = cp56.DEFAULT_TZ,
        reconnect_s: float = 5.0,
    ):
        self.host, self.port, self.ca = host, port, ca
        self._on_data, self._on_state, self._on_frame = on_data, on_state, on_frame
        self.params, self.cycles, self.tz, self.reconnect_s = (
            params,
            cycles,
            tz,
            reconnect_s,
        )
        self.link: Link | None = None
        self.state = "down"
        # 等待中的确认：(类型, IOA, 期望 COT) → future；结果为 None 表示链路已断开
        self._waiters: dict[tuple[TypeID, int, COT], asyncio.Future[Asdu | None]] = {}
        self._cmd_lock = asyncio.Lock()
        self._stopping = False
        # 召唤/对时/命令统计（次数、最近一次完成时间 ms、耗时 ms）
        self.stats: dict[str, int] = {"gi": 0, "clock_sync": 0, "counter_gi": 0, "commands": 0}

    # ------------------------------------------------------------ 生命周期

    async def run_forever(self) -> None:
        while not self._stopping:
            reason = await self._session()
            await self._set_state("down", reason)
            if not self._stopping:
                await asyncio.sleep(self.reconnect_s)

    def stop(self) -> None:
        self._stopping = True
        if self.link:
            self.link.close("client stopping")

    async def _set_state(self, state: str, detail: str = "") -> None:
        self.state = state
        if self._on_state:
            await self._on_state(state, detail)

    async def _session(self) -> str:
        await self._set_state("connecting", f"{self.host}:{self.port}")
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=self.params.t0
            )
        except (OSError, TimeoutError) as e:
            return f"connect failed: {type(e).__name__} {e}"

        link = Link(
            reader,
            writer,
            on_asdu=self._on_asdu,
            params=self.params,
            tz=self.tz,
            on_frame=self._on_frame,
            name=f"{self.host}:{self.port}",
        )
        self.link = link
        link.add_close_callback(lambda _: self._release_waiters())
        runner = asyncio.create_task(link.run())
        cyclic: asyncio.Task | None = None
        try:
            await link.start_dt()
            await self._set_state("up")
            await self.clock_sync()
            await self.interrogation()
            await self.counter_interrogation()
            cyclic = asyncio.create_task(self._cyclic())
            return await runner
        except (LinkClosed, TimeoutError) as e:
            link.close(f"startup failed: {type(e).__name__} {e}")
            return await runner
        finally:
            if cyclic:
                cyclic.cancel()
            self._release_waiters()
            self.link = None
            if not runner.done():
                link.close("session cancelled")
                await asyncio.gather(runner, return_exceptions=True)

    def _release_waiters(self) -> None:
        for fut in self._waiters.values():
            if not fut.done():
                fut.set_result(None)

    async def _cyclic(self) -> None:
        loop = asyncio.get_running_loop()
        c = self.cycles
        due = {
            "gi": loop.time() + c.gi_s,
            "cs": loop.time() + c.clock_s,
            "ci": loop.time() + c.counters_s,
        }
        jobs = {"gi": (self.interrogation, c.gi_s), "cs": (self.clock_sync, c.clock_s),
                "ci": (self.counter_interrogation, c.counters_s)}  # fmt: skip
        while True:
            name = min(due, key=due.__getitem__)
            await asyncio.sleep(max(0.0, due[name] - loop.time()))
            fn, period = jobs[name]
            due[name] = loop.time() + period
            try:
                await fn()
            except (LinkClosed, TimeoutError) as e:
                log.warning("cyclic %s failed: %s", name, e)

    # ------------------------------------------------------------ 召唤与对时

    async def _request(self, a: Asdu, until: COT, timeout: float) -> Asdu:
        """发送激活，等待指定 COT（激活确认/终止）的回复。"""
        if not self.link or self.link.closed:
            raise LinkClosed("not connected")
        ioa = a.objects[0].ioa
        loop = asyncio.get_running_loop()
        con = self._waiters.setdefault((a.type_id, ioa, COT.ACTIVATION_CON), loop.create_future())
        end = (
            self._waiters.setdefault((a.type_id, ioa, until), loop.create_future())
            if until != COT.ACTIVATION_CON
            else con
        )
        try:
            await self.link.send_asdu(a)
            async with asyncio.timeout(timeout):
                first = await con
                if first is None:
                    raise LinkClosed(self.link.close_reason if self.link else "link closed")
                if first.negative or end is con:
                    return first
                last = await end
                if last is None:
                    raise LinkClosed("link closed before termination")
                return last
        finally:
            for c in (COT.ACTIVATION_CON, until):
                self._waiters.pop((a.type_id, ioa, c), None)

    def _count(self, name: str, reply: Asdu, started: float) -> Asdu:
        if not reply.negative:
            self.stats[name] += 1
            self.stats[f"last_{name}"] = int(datetime.now(UTC).timestamp() * 1000)
            self.stats[f"{name}_ms"] = round((asyncio.get_running_loop().time() - started) * 1000)
        return reply

    async def interrogation(self, timeout: float = 30.0) -> Asdu:
        t0 = asyncio.get_running_loop().time()
        a = Asdu(TypeID.C_IC_NA_1, COT.ACTIVATION, self.ca, [InfoObject(0, QOI_STATION)])
        return self._count("gi", await self._request(a, COT.ACTIVATION_TERM, timeout), t0)

    async def counter_interrogation(self, timeout: float = 30.0) -> Asdu:
        t0 = asyncio.get_running_loop().time()
        a = Asdu(TypeID.C_CI_NA_1, COT.ACTIVATION, self.ca, [InfoObject(0, QCC_GENERAL)])
        return self._count("counter_gi", await self._request(a, COT.ACTIVATION_TERM, timeout), t0)

    async def clock_sync(self, timeout: float = 10.0) -> Asdu:
        t0 = asyncio.get_running_loop().time()
        a = Asdu(TypeID.C_CS_NA_1, COT.ACTIVATION, self.ca, [InfoObject(0, datetime.now(UTC))])
        return self._count("clock_sync", await self._request(a, COT.ACTIVATION_CON, timeout), t0)

    # ------------------------------------------------------------ 命令

    async def command(
        self,
        type_id: TypeID,
        ioa: int,
        value: Any,
        *,
        select: bool,
        timeout: float = 10.0,
        qualifier: int = 0,
    ) -> CommandResult:
        """遥控/遥调。select=True 只等激活确认；执行命令还要等激活终止（动作完成）。"""
        if type_id not in COMMAND_TYPES:
            raise ValueError(f"{type_id} is not a command type")
        obj = InfoObject(ioa, value, select=select, qualifier=qualifier)
        a = Asdu(type_id, COT.ACTIVATION, self.ca, [obj])
        until = COT.ACTIVATION_CON if select else COT.ACTIVATION_TERM
        self.stats["commands"] += 1
        async with self._cmd_lock:  # 同一时刻只允许一个命令在途
            try:
                reply = await self._request(a, until, timeout)
            except TimeoutError:
                return CommandResult(False, "timeout", "no confirmation within timeout")
            except LinkClosed as e:
                return CommandResult(False, "link", str(e))
        stage = "actterm" if reply.cot == COT.ACTIVATION_TERM else "actcon"
        return CommandResult(not reply.negative, stage, reply.cot.name)

    async def cancel_select(self, type_id: TypeID, ioa: int, value: Any, timeout: float = 10.0) -> bool:
        if not self.link:
            return False
        loop = asyncio.get_running_loop()
        fut = self._waiters.setdefault((type_id, ioa, COT.DEACTIVATION_CON), loop.create_future())
        try:
            obj = InfoObject(ioa, value, select=True)
            await self.link.send_asdu(Asdu(type_id, COT.DEACTIVATION, self.ca, [obj]))
            async with asyncio.timeout(timeout):
                reply = await fut
                return reply is not None and not reply.negative
        except (TimeoutError, LinkClosed):
            return False
        finally:
            self._waiters.pop((type_id, ioa, COT.DEACTIVATION_CON), None)

    # ------------------------------------------------------------ 接收

    async def _on_asdu(self, link: Link, a: Asdu | None, raw: bytes) -> None:
        if a is None:
            return
        if a.cot in (COT.ACTIVATION_CON, COT.ACTIVATION_TERM, COT.DEACTIVATION_CON) or (
            a.negative and a.cot in (COT.UNKNOWN_TYPE, COT.UNKNOWN_CAUSE, COT.UNKNOWN_CA, COT.UNKNOWN_IOA)
        ):
            ioa = a.objects[0].ioa if a.objects else 0
            cot = COT.ACTIVATION_CON if a.cot >= COT.UNKNOWN_TYPE else a.cot
            fut = self._waiters.get((a.type_id, ioa, cot))
            if fut and not fut.done():
                fut.set_result(a)
            return
        await self._on_data(a)
