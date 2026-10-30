import pytest
from fastapi.testclient import TestClient

from app.core import checks
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_live(client):
    r = client.get("/api/health/live")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def _fake_check(db_status: str):
    async def fake(pool, redis):
        return {
            "database": {"status": db_status, "latency_ms": 1.0},
            "redis": {"status": "ok", "latency_ms": 0.5},
        }

    return fake


def test_ready_ok(client, monkeypatch):
    monkeypatch.setattr(checks, "check_all", _fake_check("ok"))
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert set(body["components"]) == {"database", "redis"}


def test_ready_degraded_returns_503(client, monkeypatch):
    monkeypatch.setattr(checks, "check_all", _fake_check("down"))
    r = client.get("/api/health")
    assert r.status_code == 503
    assert r.json()["status"] == "degraded"


async def test_timed_swallows_errors():
    async def boom():
        raise ConnectionRefusedError("db unreachable")

    result = await checks._timed(boom())
    assert result["status"] == "down"
    assert "ConnectionRefusedError" in result["error"]
