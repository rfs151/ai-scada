"""IEC 104 链路层：一个 TCP 连接上的 I/S/U 帧收发与流量控制。主站、子站共用。

参数（IEC 60870-5-104 §5，默认值）:
    k  = 12  发送方未被确认的 I 帧最大数目，达到后暂停发送
    w  = 8   接收方最迟在收到 w 个 I 帧后发送确认
    t1 = 15s 发送 I 帧或 U 帧(测试/启动)后等待确认的超时，超时关闭连接
    t2 = 10s 无数据报文时发送 S 帧确认的超时（t2 < t1）
    t3 = 20s 长时间空闲时发送 TESTFR 测试帧的超时
"""

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import tzinfo

from . import apci, cp56
from . import asdu as asdu_mod
from .apci import Frame, Kind, UFunc

log = logging.getLogger(__name__)

FrameHook = Callable[[str, bytes], None]  # (方向 "tx"/"rx", 原始 APDU)
AsduHandler = Callable[["Link", asdu_mod.Asdu | None, bytes], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class LinkParams:
    k: int = 12
    w: int = 8
    t0: float = 30.0  # 建立连接超时（主站用）
    t1: float = 15.0
    t2: float = 10.0
    t3: float = 20.0

    def __post_init__(self):
        if self.w * 3 > self.k * 2 or self.t2 >= self.t1:
            # 规约建议 w ≤ 2/3·k，t2 < t1
            raise ValueError("invalid link parameters: require w <= 2/3*k and t2 < t1")


class LinkClosed(ConnectionError):
    pass


class Link:
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        on_asdu: AsduHandler,
        params: LinkParams = LinkParams(),
        tz: tzinfo = cp56.DEFAULT_TZ,
        on_frame: FrameHook | None = None,
        on_started: Callable[["Link"], Awaitable[None]] | None = None,
        name: str = "",
    ):
        self.reader, self.writer = reader, writer
        self.params, self.tz, self.name = params, tz, name or self._peer(writer)
        self._on_asdu, self._on_frame, self._on_started = on_asdu, on_frame, on_started

        self.started = False  # 数据传输已启动（STARTDT 后）
        self.vs = 0  # 发送序号 V(S)
        self.vr = 0  # 接收序号 V(R)
        self.ack = 0  # 对端已确认到的序号
        self._sent: deque[tuple[int, float]] = deque()  # 未确认 I 帧 (序号, 发送时刻)
        self._recv_unacked = 0
        self._recv_unacked_since = 0.0
        self._last_rx = time.monotonic()
        self._u_pending: dict[UFunc, tuple[asyncio.Future, float]] = {}

        self._window = asyncio.Condition()
        self._inbox: asyncio.Queue[bytes] = asyncio.Queue()
        self._closed = asyncio.Event()
        self._close_callbacks: list[Callable[[str], None]] = []
        self.close_reason = ""
        self.stats = {"tx_i": 0, "rx_i": 0, "tx_s": 0, "rx_s": 0, "tx_u": 0, "rx_u": 0}

    @staticmethod
    def _peer(writer: asyncio.StreamWriter) -> str:
        p = writer.get_extra_info("peername")
        return f"{p[0]}:{p[1]}" if p else "?"

    # ------------------------------------------------------------ 公共接口

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    @property
    def unacked(self) -> int:
        return apci.seq_distance(self.ack, self.vs)

    async def run(self) -> str:
        """运行直到连接关闭，返回关闭原因。"""
        tasks = [
            asyncio.create_task(self._reader_loop(), name=f"104-rx-{self.name}"),
            asyncio.create_task(self._timer_loop(), name=f"104-timer-{self.name}"),
            asyncio.create_task(self._dispatch_loop(), name=f"104-dispatch-{self.name}"),
        ]
        try:
            await self._closed.wait()
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.writer.close()
            with contextlib.suppress(Exception):
                await self.writer.wait_closed()
            for fut, _ in self._u_pending.values():
                if not fut.done():
                    fut.set_result(False)  # False = 链路已关闭
            async with self._window:
                self._window.notify_all()
        return self.close_reason

    def close(self, reason: str) -> None:
        if not self._closed.is_set():
            self.close_reason = reason
            log.info("[%s] link closed: %s", self.name, reason)
            self._closed.set()
            for cb in self._close_callbacks:
                cb(reason)

    def add_close_callback(self, cb: Callable[[str], None]) -> None:
        self._close_callbacks.append(cb)

    async def send_asdu(self, a: asdu_mod.Asdu) -> None:
        """发送 I 帧；发送窗口满（未确认数 = k）时等待对端确认。"""
        await self.send_raw_asdu(asdu_mod.encode(a, self.tz))

    async def send_raw_asdu(self, data: bytes) -> None:
        async with self._window:
            await self._window.wait_for(
                lambda: self.closed or (self.started and self.unacked < self.params.k)
            )
            if self.closed:
                raise LinkClosed(self.close_reason)
            self._write(apci.encode_i(self.vs, self.vr, data))
            self._sent.append((self.vs, time.monotonic()))
            self.vs = (self.vs + 1) % apci.SEQ_MOD
            self._recv_unacked = 0  # I 帧捎带确认
            self.stats["tx_i"] += 1
        await self.writer.drain()

    async def start_dt(self) -> None:
        """主站：发送 STARTDT 激活并等待确认。"""
        await self._u_request(UFunc.STARTDT_ACT, UFunc.STARTDT_CON)
        self.started = True
        async with self._window:
            self._window.notify_all()

    # ------------------------------------------------------------ 内部

    def _write(self, raw: bytes) -> None:
        if self._on_frame:
            self._on_frame("tx", raw)
        self.writer.write(raw)

    def _write_u(self, func: UFunc) -> None:
        self._write(apci.encode_u(func))
        self.stats["tx_u"] += 1

    async def _u_request(self, act: UFunc, con: UFunc) -> None:
        fut = asyncio.get_running_loop().create_future()
        self._u_pending[con] = (fut, time.monotonic())
        self._write_u(act)
        await self.writer.drain()
        if not await fut:
            raise LinkClosed(self.close_reason)

    def _send_s(self) -> None:
        self._write(apci.encode_s(self.vr))
        self._recv_unacked = 0
        self.stats["tx_s"] += 1

    async def _reader_loop(self) -> None:
        try:
            while True:
                raw = await apci.read_apdu(self.reader)
                self._last_rx = time.monotonic()
                if self._on_frame:
                    self._on_frame("rx", raw)
                await self._handle(apci.decode(raw))
        except (asyncio.IncompleteReadError, ConnectionError) as e:
            self.close(f"connection lost ({type(e).__name__})")
        except apci.FrameError as e:
            self.close(f"protocol error: {e}")
        except LinkClosed:
            pass

    async def _handle(self, f: Frame) -> None:
        if f.kind is Kind.I:
            self.stats["rx_i"] += 1
            if f.ns != self.vr:
                raise apci.FrameError(f"sequence error: got N(S)={f.ns}, expected {self.vr}")
            self.vr = (self.vr + 1) % apci.SEQ_MOD
            await self._process_ack(f.nr)
            if self._recv_unacked == 0:
                self._recv_unacked_since = time.monotonic()
            self._recv_unacked += 1
            if self._recv_unacked >= self.params.w:
                self._send_s()
            self._inbox.put_nowait(f.asdu)
        elif f.kind is Kind.S:
            self.stats["rx_s"] += 1
            await self._process_ack(f.nr)
        else:
            self.stats["rx_u"] += 1
            await self._handle_u(f.ufunc)

    async def _process_ack(self, nr: int) -> None:
        if apci.seq_distance(self.ack, nr) > self.unacked:
            raise apci.FrameError(f"ack error: N(R)={nr} outside window [{self.ack}, {self.vs}]")
        for _ in range(apci.seq_distance(self.ack, nr)):
            self._sent.popleft()
        self.ack = nr
        async with self._window:
            self._window.notify_all()

    async def _handle_u(self, func: UFunc | None) -> None:
        match func:
            case UFunc.STARTDT_ACT:
                self._write_u(UFunc.STARTDT_CON)
                was_started, self.started = self.started, True
                async with self._window:
                    self._window.notify_all()
                if not was_started and self._on_started:
                    asyncio.create_task(self._on_started(self))
            case UFunc.STOPDT_ACT:
                if self._recv_unacked:
                    self._send_s()
                self.started = False
                self._write_u(UFunc.STOPDT_CON)
            case UFunc.TESTFR_ACT:
                self._write_u(UFunc.TESTFR_CON)
            case UFunc.STARTDT_CON | UFunc.STOPDT_CON | UFunc.TESTFR_CON:
                pending = self._u_pending.pop(func, None)
                if pending and not pending[0].done():
                    pending[0].set_result(True)

    async def _timer_loop(self) -> None:
        p = self.params
        while True:
            await asyncio.sleep(0.2)
            now = time.monotonic()
            if self._sent and now - self._sent[0][1] >= p.t1:
                self.close(f"t1 timeout: I-frame {self._sent[0][0]} not acknowledged")
                return
            for con, (_, sent_at) in self._u_pending.items():
                if now - sent_at >= p.t1:
                    self.close(f"t1 timeout: no {con.name}")
                    return
            if self._recv_unacked and now - self._recv_unacked_since >= p.t2:
                self._send_s()
            if now - self._last_rx >= p.t3 and UFunc.TESTFR_CON not in self._u_pending:
                self._last_rx = now  # 防止重复发送；TESTFR 自身有 t1 超时保护
                self._u_pending[UFunc.TESTFR_CON] = (
                    asyncio.get_running_loop().create_future(),
                    now,
                )
                self._write_u(UFunc.TESTFR_ACT)

    async def _dispatch_loop(self) -> None:
        # 独立任务处理 ASDU：处理函数内部可以 send_asdu 并等待窗口，不会阻塞接收确认
        while True:
            raw = await self._inbox.get()
            try:
                parsed = asdu_mod.decode(raw, self.tz)
            except asdu_mod.AsduError as e:
                log.warning("[%s] bad ASDU %s: %s", self.name, raw.hex(" "), e)
                parsed = None
            try:
                await self._on_asdu(self, parsed, raw)
            except LinkClosed:
                return
            except Exception:
                log.exception("[%s] ASDU handler failed", self.name)
