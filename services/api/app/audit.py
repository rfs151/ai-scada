"""审计日志：登录、遥控各步骤、挂牌、告警确认。只追加，不提供修改/删除接口。"""

import json
import logging

import asyncpg

from app.auth.security import User

log = logging.getLogger(__name__)


async def write(
    pool: asyncpg.Pool,
    action: str,
    *,
    user: User | None = None,
    username: str | None = None,
    target: str = "",
    ok: bool = True,
    detail: dict | None = None,
    ip: str | None = None,
) -> None:
    try:
        await pool.execute(
            "INSERT INTO audit_log (username, role, action, target, ok, detail, ip) VALUES ($1,$2,$3,$4,$5,$6,$7)",
            user.username if user else username, user.role if user else None, action, target, ok,
            json.dumps(detail, ensure_ascii=False) if detail is not None else None, ip,
        )  # fmt: skip
    except Exception:  # noqa: BLE001 - 审计写失败不应掩盖业务结果，但必须留下错误日志
        log.exception("audit write failed: %s %s", action, target)
