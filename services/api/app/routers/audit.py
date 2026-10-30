import json

from fastapi import APIRouter, Depends, Query, Request

from app.auth.deps import require
from app.auth.security import User

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("", summary="审计日志（监护员、管理员可查）")
async def list_audit(
    request: Request,
    limit: int = Query(200, ge=1, le=2000),
    action: str | None = Query(None, description="前缀匹配，如 control"),
    username: str | None = None,
    _: User = Depends(require("audit")),
) -> list[dict]:
    rows = await request.app.state.pool.fetch(
        """SELECT * FROM audit_log
           WHERE ($2::text IS NULL OR action LIKE $2 || '%') AND ($3::text IS NULL OR username = $3)
           ORDER BY id DESC LIMIT $1""",
        limit, action, username,
    )  # fmt: skip
    return [
        {"id": r["id"], "ts": int(r["ts"].timestamp() * 1000), "username": r["username"], "role": r["role"],
         "action": r["action"], "target": r["target"], "ok": r["ok"], "detail": json.loads(r["detail"]) if r["detail"] else None, "ip": r["ip"]}
        for r in rows
    ]  # fmt: skip
