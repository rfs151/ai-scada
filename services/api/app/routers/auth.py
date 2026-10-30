from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app import audit
from app.auth.deps import client_ip, optional_user
from app.auth.security import ROLE_CN, User, create_token
from app.auth.users import DEMO_USERS, authenticate

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginReq(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


@router.get("/config", summary="登录页配置（演示模式下给出演示账号）")
async def config(request: Request) -> dict:
    s = request.app.state.settings
    if not s.demo_mode:
        return {"demo": False}
    return {
        "demo": True,
        "password": s.demo_password,
        "accounts": [{"username": u, "name": n, "role": r, "role_cn": ROLE_CN[r]} for u, n, r in DEMO_USERS],
    }


@router.post("/login", summary="登录，返回访问令牌")
async def login(req: LoginReq, request: Request) -> dict:
    st = request.app.state
    ip = client_ip(request) or "?"
    keys = (f"u:{req.username}", f"ip:{ip}")
    if st.login_throttle.blocked(*keys):
        await audit.write(
            st.pool, "login", username=req.username, ok=False, detail={"reason": "locked"}, ip=ip
        )
        raise HTTPException(429, "失败次数过多，请 5 分钟后再试")
    user = await authenticate(st.pool, req.username, req.password)
    if user is None:
        st.login_throttle.fail(*keys)
        await audit.write(st.pool, "login", username=req.username, ok=False, detail={"reason": "bad credentials"},
                          ip=ip)  # fmt: skip
        raise HTTPException(401, "用户名或口令错误")
    st.login_throttle.reset(*keys)
    await audit.write(st.pool, "login", user=user, ip=ip)
    token = create_token(user, st.jwt_secret, st.settings.token_ttl_min * 60)
    return {"token": token, "user": user.to_dict(), "expires_in": st.settings.token_ttl_min * 60}


@router.get("/me", summary="当前用户")
async def me(user: User | None = Depends(optional_user)) -> dict:
    return {"user": user.to_dict() if user else None}
