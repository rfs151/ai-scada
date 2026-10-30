import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app import audit
from app.auth.deps import client_ip, require
from app.auth.security import User
from app.control.state import read_positions
from app.control.tags import KIND_CN

router = APIRouter(prefix="/api/tags", tags=["tags"])


class TagReq(BaseModel):
    device: str
    kind: str
    node: str | None = None
    note: str = Field("", max_length=200)


@router.get("", summary="当前挂牌")
async def list_tags(request: Request) -> list[dict]:
    return await request.app.state.tags.all()


@router.post("", summary="挂牌")
async def add_tag(req: TagReq, request: Request, user: User = Depends(require("tag"))) -> dict:
    st = request.app.state
    try:
        eq = st.station.equip(req.device)
    except StopIteration:
        raise HTTPException(404, f"设备 {req.device} 不存在") from None
    if req.kind not in KIND_CN:
        raise HTTPException(400, "挂牌类型只能是 maint / noclose / ground")
    node = None
    if req.kind == "noclose" and eq.type not in ("breaker", "disconnector"):
        raise HTTPException(400, "禁止合闸牌只能挂在断路器或刀闸上")
    if req.kind == "ground":
        if req.node not in eq.nodes:
            raise HTTPException(400, f"接地线挂接位置须为该设备的连接节点：{', '.join(eq.nodes)}")
        node = req.node
        chk = st.interlock.check_ground(
            node, await read_positions(st.redis, st.station), await st.tags.infos()
        )
        if not chk.ok:
            await audit.write(st.pool, "tag.add", user=user, target=req.device, ok=False,
                              detail={"kind": req.kind, "node": node, "reason": chk.violations[0].text},
                              ip=client_ip(request))  # fmt: skip
            raise HTTPException(409, chk.violations[0].text)
    try:
        tag = await st.tags.add(req.device, req.kind, node, req.note, user.name)
    except asyncpg.UniqueViolationError:
        raise HTTPException(409, f"{req.device} 已挂{KIND_CN[req.kind]}牌") from None
    await audit.write(st.pool, "tag.add", user=user, target=req.device, detail=tag, ip=client_ip(request))
    return tag


@router.delete("/{tag_id}", summary="摘牌")
async def remove_tag(tag_id: int, request: Request, user: User = Depends(require("tag"))) -> dict:
    st = request.app.state
    tag = await st.tags.remove(tag_id)
    if tag is None:
        raise HTTPException(404, "挂牌不存在")
    await audit.write(
        st.pool, "tag.remove", user=user, target=tag["device"], detail=tag, ip=client_ip(request)
    )
    return tag
