from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response, status

from app.core import checks

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("/live", summary="存活探针（不检查依赖）")
async def live() -> dict:
    return {"status": "ok"}


@router.get("", summary="就绪探针：检查数据库与实时库")
async def ready(request: Request, response: Response) -> dict:
    state = request.app.state
    components = await checks.check_all(state.pool, state.redis)
    healthy = all(c["status"] == "ok" for c in components.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ok" if healthy else "degraded",
        "version": state.settings.app_version,
        "server_time": datetime.now(UTC).isoformat(),
        "uptime_s": round((datetime.now(UTC) - state.started_at).total_seconds()),
        "components": components,
        "workers": state.workers.status() if state.workers else None,
    }
