from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.auth.deps import client_ip, require
from app.auth.security import User
from app.control.service import ControlError

router = APIRouter(prefix="/api/control", tags=["control"])


def _svc(request: Request):
    return request.app.state.control


async def _call(coro):
    try:
        return await coro
    except ControlError as e:
        raise HTTPException(e.status, str(e)) from None


class CreateReq(BaseModel):
    point: str
    action: str
    confirm: str = Field(description="复诵：手工输入的设备编号")


class SuperviseReq(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=128)


class AdjustReq(BaseModel):
    point: str
    value: Any


@router.get("/sessions", summary="最近的遥控会话")
async def sessions(request: Request) -> list[dict]:
    return _svc(request).list()


@router.get("/check", summary="五防预校验（只读）")
async def check(request: Request, point: str, action: str = Query(pattern="^(open|close)$")) -> dict:
    r = await _call(_svc(request).preview(point, action))
    return r.to_dict()


@router.post("/sessions", summary="发起遥控（五防校验 + 复诵）")
async def create(req: CreateReq, request: Request, user: User = Depends(require("control"))) -> dict:
    return await _call(_svc(request).create(user, req.point, req.action, req.confirm, client_ip(request)))


@router.post("/sessions/{sid}/supervise", summary="监护人确认")
async def supervise(
    sid: str, req: SuperviseReq, request: Request, user: User = Depends(require("control"))
) -> dict:
    st = request.app.state
    ip = client_ip(request) or "?"
    keys = (f"u:{req.username}", f"ip:{ip}")
    if st.login_throttle.blocked(*keys):
        raise HTTPException(429, "监护人口令错误次数过多，请 5 分钟后再试")
    try:
        return await _svc(request).supervise(sid, user, req.username, req.password, ip)
    except ControlError as e:
        if "口令" in str(e):
            st.login_throttle.fail(*keys)
        raise HTTPException(e.status, str(e)) from None


@router.post("/sessions/{sid}/select", summary="选择（等待子站返校）")
async def select(sid: str, request: Request, user: User = Depends(require("control"))) -> dict:
    return await _call(_svc(request).select(sid, user, client_ip(request)))


@router.post("/sessions/{sid}/execute", summary="执行")
async def execute(sid: str, request: Request, user: User = Depends(require("control"))) -> dict:
    return await _call(_svc(request).execute(sid, user, client_ip(request)))


@router.post("/sessions/{sid}/cancel", summary="撤销")
async def cancel(sid: str, request: Request, user: User = Depends(require("control"))) -> dict:
    return await _call(_svc(request).cancel(sid, user, client_ip(request)))


@router.post("/adjust", summary="遥调：主变升降档 / 光伏有功设定")
async def adjust(req: AdjustReq, request: Request, user: User = Depends(require("control"))) -> dict:
    return await _call(_svc(request).adjust(user, req.point, req.value, client_ip(request)))
