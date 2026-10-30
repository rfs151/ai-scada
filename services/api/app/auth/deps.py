"""FastAPI 依赖：从 Authorization: Bearer 令牌解析当前用户，并按权限放行。"""

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.security import User, decode_token

bearer = HTTPBearer(auto_error=False)


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def optional_user(
    request: Request, cred: HTTPAuthorizationCredentials | None = Depends(bearer)
) -> User | None:
    if cred is None:
        return None
    try:
        return decode_token(cred.credentials, request.app.state.jwt_secret)
    except jwt.InvalidTokenError as e:
        raise HTTPException(401, "登录已失效，请重新登录", headers={"WWW-Authenticate": "Bearer"}) from e


def require(perm: str):
    async def dep(user: User | None = Depends(optional_user)) -> User:
        if user is None:
            raise HTTPException(401, "请先登录", headers={"WWW-Authenticate": "Bearer"})
        if not user.can(perm):
            raise HTTPException(403, f"当前角色无此权限：{perm}")
        return user

    return dep
