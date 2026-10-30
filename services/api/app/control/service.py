"""遥控会话：发起（五防 + 复诵）→ 监护确认 → 选择（返校）→ 执行 → 完成。

状态机：
    pending ──监护──▶ authorized ──选择/返校成功──▶ selected ──执行成功──▶ done
       │                 │                            │
       └──── 超时 expired / 撤销 cancelled / 五防或子站拒绝 rejected、failed ◀┘

设计要点：
  - 监护在选择之前：子站选择超时仅 30s，监护人输入口令不应占用这段时间
  - 选择、执行前都重新做五防校验：发起后现场状态可能已经变化
  - 同一设备同时只允许一个未结束的会话
  - 执行前在 Redis 记录"该设备正在被遥控"，告警引擎据此区分遥控变位与事故/就地变位
  - 每一步都写审计日志，并经事件总线推送进度
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from redis.asyncio import Redis

from app.auth.security import User
from app.control.interlock import CheckResult, Interlock, TagInfo
from app.realtime import keys
from scadalib.model import Cat, Station

log = logging.getLogger(__name__)

ACTIVE = {"pending", "authorized", "selected"}
STATE_CN = {
    "pending": "待监护",
    "authorized": "已授权，待选择",
    "selected": "返校成功，待执行",
    "done": "执行成功",
    "rejected": "五防闭锁",
    "failed": "失败",
    "cancelled": "已撤销",
    "expired": "已超时",
}
ACTION_CN = {"close": "合闸", "open": "分闸", "raise": "升档", "lower": "降档", "set": "设定"}

# 子站选择超时 30s，留 2s 余量
SELECT_WINDOW_S = 28.0


class ControlError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Transport(Protocol):
    async def send(self, point: str, action: str, value: Any, timeout: float) -> dict: ...


class FepTransport:
    """经 Redis Stream 把命令交给前置机，等待结果列表 cmd:res:<id>。"""

    def __init__(self, redis: Redis):
        self.redis = redis

    async def send(self, point: str, action: str, value: Any, timeout: float) -> dict:
        rid = uuid.uuid4().hex
        req = {"id": rid, "point": point, "action": action, "value": value, "timeout": timeout}
        await self.redis.xadd(keys.CMD_REQ, {"r": json.dumps(req)}, maxlen=1000, approximate=True)
        res = await self.redis.blpop([keys.cmd_result(rid)], timeout=timeout + 5)
        if res is None:
            return {"ok": False, "stage": "timeout", "detail": "前置机无响应（通道中断或前置机离线）"}
        return json.loads(res[1])


@dataclass
class Step:
    t: int
    step: str
    ok: bool
    detail: str = ""
    user: str | None = None


@dataclass
class Session:
    id: str
    point: str
    device: str
    device_name: str
    action: str
    operator: User
    created: float
    deadline: float
    state: str = "pending"
    supervisor: User | None = None
    select_deadline: float | None = None
    check: CheckResult | None = None
    steps: list[Step] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def log(self, step: str, ok: bool, detail: str = "", user: User | None = None) -> None:
        self.steps.append(Step(int(time.time() * 1000), step, ok, detail, user.name if user else None))

    def to_dict(self, now: float) -> dict:
        remain = (self.select_deadline if self.state == "selected" else self.deadline) - now
        return {
            "id": self.id,
            "point": self.point,
            "device": self.device,
            "device_name": self.device_name,
            "action": self.action,
            "action_cn": ACTION_CN[self.action],
            "state": self.state,
            "state_cn": STATE_CN[self.state],
            "operator": self.operator.to_dict(),
            "supervisor": self.supervisor.to_dict() if self.supervisor else None,
            "remaining_s": round(max(0.0, remain), 1) if self.state in ACTIVE else None,
            "check": self.check.to_dict() if self.check else None,
            "steps": [s.__dict__ for s in self.steps],
        }


Auditor = Callable[..., Awaitable[None]]
Publisher = Callable[[dict], Awaitable[None]]
PositionReader = Callable[[], Awaitable[Callable[[str], int | None]]]
TagReader = Callable[[], Awaitable[list[TagInfo]]]
Authenticator = Callable[[str, str], Awaitable[User | None]]


class ControlService:
    def __init__(
        self,
        st: Station,
        interlock: Interlock,
        transport: Transport,
        *,
        positions: PositionReader,
        tags: TagReader,
        authenticate: Authenticator,
        audit: Auditor,
        publish: Publisher,
        mark_commanded: Callable[[str, dict], Awaitable[None]],
        session_s: float = 120.0,
        cmd_timeout_s: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.st, self.interlock, self.transport = st, interlock, transport
        self.positions, self.tags, self.authenticate = positions, tags, authenticate
        self.audit, self.publish, self.mark_commanded = audit, publish, mark_commanded
        self.session_s, self.cmd_timeout_s, self.clock = session_s, cmd_timeout_s, clock
        self.sessions: dict[str, Session] = {}

    # ------------------------------------------------------------ 查询

    def list(self) -> list[dict]:
        now = self.clock()
        items = sorted(self.sessions.values(), key=lambda s: s.created, reverse=True)
        return [s.to_dict(now) for s in items[:30]]

    def get(self, sid: str) -> Session:
        s = self.sessions.get(sid)
        if s is None:
            raise ControlError(404, "遥控会话不存在或已清理")
        return s

    async def preview(self, point: str, action: str) -> CheckResult:
        """五防预校验（不发起会话），供设备面板展示。"""
        p = self._yk_point(point)
        return self.interlock.check(p.ref, action, await self.positions(), await self.tags())

    # ------------------------------------------------------------ 流程

    async def create(self, user: User, point: str, action: str, confirm: str, ip: str | None = None) -> dict:
        if action not in ("open", "close"):
            raise ControlError(400, "遥控动作只能是 open / close")
        p = self._yk_point(point)
        dev = p.ref
        # 复诵：操作员手工输入设备编号，防止误选设备
        if confirm.strip() != dev:
            await self.audit(
                "control.create", user=user, target=dev, ok=False, detail={"reason": "复诵不一致"}, ip=ip
            )
            raise ControlError(400, f"设备编号复诵不一致：应为 {dev}")
        self._expire()
        for s in self.sessions.values():
            if s.device == dev and s.state in ACTIVE:
                raise ControlError(409, f"{dev} 已有进行中的遥控（{s.operator.name}，{STATE_CN[s.state]}）")

        now = self.clock()
        s = Session(
            uuid.uuid4().hex[:12],
            point,
            dev,
            self.st.equip(dev).name,
            action,
            user,
            now,
            now + self.session_s,
        )
        s.check = self.interlock.check(dev, action, await self.positions(), await self.tags())
        if s.check.ok:
            s.log("发起", True, f"五防校验通过，复诵 {dev} 正确", user)
        else:
            s.state = "rejected"
            s.log("五防校验", False, "；".join(v.text for v in s.check.violations), user)
        self.sessions[s.id] = s
        await self.audit("control.create", user=user, target=dev, ok=s.check.ok,
                         detail={"session": s.id, "action": action, "violations": [v.text for v in s.check.violations]},
                         ip=ip)  # fmt: skip
        await self._push(s)
        return s.to_dict(self.clock())

    async def supervise(
        self, sid: str, user: User, username: str, password: str, ip: str | None = None
    ) -> dict:
        s = self.get(sid)
        async with s.lock:
            self._own(s, user)
            self._expect(s, "pending")
            sup = await self.authenticate(username, password)
            reason = None
            if sup is None:
                reason = "监护人用户名或口令错误"
            elif not sup.can("supervise"):
                reason = f"{sup.name} 不具备监护权限"
            elif sup.username == s.operator.username:
                reason = "监护人不能是操作人本人"
            await self.audit("control.supervise", username=username, target=s.device, ok=reason is None,
                             detail={"session": s.id, "operator": s.operator.username, "reason": reason}, ip=ip)  # fmt: skip
            if reason:
                s.log("监护确认", False, reason)
                await self._push(s)
                raise ControlError(403, reason)
            s.supervisor = sup
            s.state = "authorized"
            s.log("监护确认", True, f"监护人 {sup.name}", sup)
            await self._push(s)
            return s.to_dict(self.clock())

    async def select(self, sid: str, user: User, ip: str | None = None) -> dict:
        s = self.get(sid)
        async with s.lock:
            self._own(s, user)
            self._expect(s, "authorized")
            if not await self._recheck(s, user, "选择"):
                await self._push(s)
                return s.to_dict(self.clock())
            r = await self.transport.send(s.point, "select", s.action, self.cmd_timeout_s)
            ok = bool(r.get("ok"))
            await self.audit(
                "control.select", user=user, target=s.device, ok=ok, detail={"session": s.id, **r}, ip=ip
            )
            if ok:
                s.state = "selected"
                s.select_deadline = self.clock() + SELECT_WINDOW_S
                s.log("选择返校", True, f"子站返校成功（{r.get('elapsed_ms', '?')} ms）", user)
            else:
                s.state = "failed"
                s.log("选择返校", False, _rtu_reason(r), user)
            await self._push(s)
            return s.to_dict(self.clock())

    async def execute(self, sid: str, user: User, ip: str | None = None) -> dict:
        s = self.get(sid)
        async with s.lock:
            self._own(s, user)
            self._expect(s, "selected")
            if not await self._recheck(s, user, "执行"):
                with contextlib.suppress(Exception):
                    await self.transport.send(s.point, "cancel", s.action, self.cmd_timeout_s)
                await self._push(s)
                return s.to_dict(self.clock())
            await self.mark_commanded(s.device, {"action": s.action, "operator": s.operator.name,
                                                 "supervisor": s.supervisor.name if s.supervisor else None,
                                                 "session": s.id})  # fmt: skip
            r = await self.transport.send(s.point, "execute", s.action, self.cmd_timeout_s)
            ok = bool(r.get("ok"))
            await self.audit(
                "control.execute", user=user, target=s.device, ok=ok, detail={"session": s.id, **r}, ip=ip
            )
            s.state = "done" if ok else "failed"
            s.log(
                "执行", ok, f"子站执行完毕（{r.get('elapsed_ms', '?')} ms）" if ok else _rtu_reason(r), user
            )
            await self._push(s)
            return s.to_dict(self.clock())

    async def cancel(self, sid: str, user: User, ip: str | None = None) -> dict:
        s = self.get(sid)
        async with s.lock:
            self._own(s, user)
            if s.state not in ACTIVE:
                raise ControlError(409, f"当前状态（{STATE_CN[s.state]}）不能撤销")
            if s.state == "selected":
                r = await self.transport.send(s.point, "cancel", s.action, self.cmd_timeout_s)
                s.log("撤销选择", bool(r.get("ok")), "" if r.get("ok") else _rtu_reason(r), user)
            else:
                s.log("撤销", True, "", user)
            s.state = "cancelled"
            await self.audit("control.cancel", user=user, target=s.device, detail={"session": s.id}, ip=ip)
            await self._push(s)
            return s.to_dict(self.clock())

    async def adjust(self, user: User, point: str, value: Any, ip: str | None = None) -> dict:
        """遥调（主变升降档、光伏有功设定）：常规调节，直接执行，不需监护。"""
        try:
            p = self.st.point(point)
        except KeyError:
            raise ControlError(404, f"测点 {point} 不存在") from None
        if p.cat != Cat.YT:
            raise ControlError(400, f"{point} 不是遥调点")
        if p.meas == "TAPCMD" and value not in ("raise", "lower"):
            raise ControlError(400, "调档命令只能是 raise / lower")
        if p.meas == "PSET":
            if not isinstance(value, int | float) or isinstance(value, bool):
                raise ControlError(400, "有功设定值必须是数值")
            cap = float(self.st.bay(p.bay).attrs.get("capacity_mw", 0))
            if not 0 <= value <= cap:
                raise ControlError(400, f"设定值超出范围 0~{cap} MW")
        tags = [t for t in await self.tags() if t.device == p.ref and t.kind == "maint"]
        if tags:
            raise ControlError(409, f"{p.ref} 挂检修牌，禁止遥调")
        r = await self.transport.send(point, "execute", value, self.cmd_timeout_s)
        ok = bool(r.get("ok"))
        await self.audit(
            "control.adjust", user=user, target=point, ok=ok, detail={"value": value, **r}, ip=ip
        )
        return {"ok": ok, "detail": "执行成功" if ok else _rtu_reason(r), "elapsed_ms": r.get("elapsed_ms")}

    # ------------------------------------------------------------ 超时清理

    async def sweep(self) -> None:
        for s in self._expire():
            await self.audit("control.expire", username=s.operator.username, target=s.device, ok=False,
                             detail={"session": s.id, "state": s.state})  # fmt: skip
            await self._push(s)
        # 只保留最近的已结束会话
        done = sorted((s for s in self.sessions.values() if s.state not in ACTIVE), key=lambda s: s.created)
        for s in done[:-50]:
            self.sessions.pop(s.id, None)

    async def run_sweeper(self, period_s: float = 1.0) -> None:
        while True:
            await asyncio.sleep(period_s)
            try:
                await self.sweep()
            except Exception:  # noqa: BLE001
                log.exception("control sweeper error")

    def _expire(self) -> list[Session]:
        now = self.clock()
        expired = []
        for s in self.sessions.values():
            if s.lock.locked():
                continue
            limit = s.select_deadline if s.state == "selected" else s.deadline
            if s.state in ACTIVE and limit is not None and now > limit:
                prev = s.state
                s.state = "expired"
                s.log(
                    "超时",
                    False,
                    "选择后未在时限内执行，子站已自动撤销选择" if prev == "selected" else "未在时限内完成",
                )
                expired.append(s)
        return expired

    # ------------------------------------------------------------ 内部

    def _yk_point(self, point: str):
        try:
            p = self.st.point(point)
        except KeyError:
            raise ControlError(404, f"测点 {point} 不存在") from None
        if p.cat != Cat.YK:
            raise ControlError(400, f"{point} 不是遥控点")
        return p

    def _own(self, s: Session, user: User) -> None:
        if s.operator.username != user.username:
            raise ControlError(403, f"该遥控由 {s.operator.name} 发起，只能由操作人本人继续")

    def _expect(self, s: Session, state: str) -> None:
        self._expire()
        if s.state != state:
            raise ControlError(409, f"当前状态为「{STATE_CN[s.state]}」，不能进行此操作")

    async def _recheck(self, s: Session, user: User, stage: str) -> bool:
        s.check = self.interlock.check(s.device, s.action, await self.positions(), await self.tags())
        if s.check.ok:
            return True
        s.state = "rejected"
        s.log(f"{stage}前五防复核", False, "；".join(v.text for v in s.check.violations), user)
        await self.audit("control.recheck", user=user, target=s.device, ok=False,
                         detail={"session": s.id, "violations": [v.text for v in s.check.violations]})  # fmt: skip
        return False

    async def _push(self, s: Session) -> None:
        with contextlib.suppress(Exception):
            await self.publish({"type": "control", "session": s.to_dict(self.clock())})


def _rtu_reason(r: dict) -> str:
    stage, detail = r.get("stage", ""), r.get("detail", "")
    if stage == "timeout":
        return detail or "等待子站确认超时"
    if stage in ("actcon", "deactcon") and not r.get("ok"):
        return f"子站否定确认（{detail}）：子站拒绝，可能是间隔层联锁闭锁或设备状态不符"
    return f"{stage} {detail}".strip() or "未知错误"
