"""口令散列与访问令牌。

口令：PBKDF2-SHA256（标准库实现，20 万次迭代，每用户随机盐），比较使用恒定时间算法。
令牌：JWT HS256，携带用户名、姓名、角色，有效期为一个值班班次。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

import jwt

ITERATIONS = 200_000

ROLE_CN = {"viewer": "访客", "operator": "调度员", "supervisor": "监护员", "admin": "管理员"}

# 权限 → 允许的角色。监护与操作分离：监护员可以监护也可以操作，但同一次遥控两者必须是不同的人
PERMISSIONS: dict[str, set[str]] = {
    "control": {"operator", "supervisor"},  # 发起/执行遥控、遥调
    "supervise": {"supervisor"},  # 遥控监护确认
    "tag": {"operator", "supervisor"},  # 挂牌/摘牌
    "ack": {"operator", "supervisor"},  # 告警确认
    "audit": {"supervisor", "admin"},  # 查看审计日志
    "sim": {"operator", "supervisor", "admin"},  # 仿真场景注入（仅演示系统）
}


@dataclass(frozen=True)
class User:
    username: str
    name: str
    role: str

    def can(self, perm: str) -> bool:
        return self.role in PERMISSIONS.get(perm, set())

    def to_dict(self) -> dict:
        return {
            "username": self.username,
            "name": self.name,
            "role": self.role,
            "role_cn": ROLE_CN.get(self.role, self.role),
            "perms": sorted(p for p, roles in PERMISSIONS.items() if self.role in roles),
        }


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iters))
        return hmac.compare_digest(dk, base64.b64decode(digest))
    except ValueError:
        return False


def create_token(user: User, secret: str, ttl_s: int) -> str:
    now = int(time.time())
    claims = {"sub": user.username, "name": user.name, "role": user.role, "iat": now, "exp": now + ttl_s}
    return jwt.encode(claims, secret, algorithm="HS256")


def decode_token(token: str, secret: str) -> User:
    """无效或过期令牌抛出 jwt.InvalidTokenError。"""
    c = jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp", "sub"]})
    return User(c["sub"], c.get("name", c["sub"]), c.get("role", "viewer"))


class LoginThrottle:
    """登录失败限制：同一账号或同一来源 IP 在窗口期内失败过多即临时锁定，防止口令爆破。"""

    def __init__(self, max_failures: int = 5, window_s: float = 300.0):
        self.max, self.window = max_failures, window_s
        self._fails: dict[str, list[float]] = {}

    def _recent(self, key: str, now: float) -> list[float]:
        lst = [t for t in self._fails.get(key, []) if now - t < self.window]
        self._fails[key] = lst
        return lst

    def blocked(self, *keys: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        return any(len(self._recent(k, now)) >= self.max for k in keys)

    def fail(self, *keys: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        for k in keys:
            self._recent(k, now).append(now)

    def reset(self, *keys: str) -> None:
        for k in keys:
            self._fails.pop(k, None)
