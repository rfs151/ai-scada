from fastapi.testclient import TestClient

from app.main import app


def test_ws_hello_heartbeat_and_pong():
    with TestClient(app) as client:
        app.state.settings.ws_heartbeat_s = 0.05
        with client.websocket_connect("/ws/realtime") as ws:
            assert ws.receive_json()["type"] == "hello"

            hb = ws.receive_json()
            assert hb["type"] == "heartbeat"
            assert hb["seq"] == 1

            ws.send_json({"type": "ping", "ts": 123})
            # 可能先收到心跳，跳过直到 pong
            for _ in range(10):
                msg = ws.receive_json()
                if msg["type"] == "pong":
                    assert msg["client_ts"] == 123
                    break
            else:
                raise AssertionError("no pong received")
