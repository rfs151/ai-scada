from fastapi.testclient import TestClient

from app.db import split_sql
from app.main import app


def test_station_and_points():
    with TestClient(app) as c:
        st = c.get("/api/model/station").json()
        assert st["ca"] == 1 and any(b["kind"] == "transformer" for b in st["bays"])
        assert st["sources"] == ["GRID"]
        assert st["nodes"]["T1_H"] == "HV" and st["nodes"]["T1_L"] == "LV" and st["nodes"]["BUS1"] == "LV"
        eq = {e["id"]: e for b in st["bays"] for e in b["equipment"]}
        assert eq["100"]["normal"] == "open" and eq["101"]["normal"] == "closed" and "normal" not in eq["T1"]
        yk = c.get("/api/model/points", params={"cat": "YK"}).json()
        assert yk and all(p["cat"] == "YK" and p["ioa"] >= 0x6001 for p in yk)
        f01 = c.get("/api/model/points", params={"bay": "F01"}).json()
        assert {p["meas"] for p in f01} >= {"P", "Q", "I", "COS", "POS", "PROT"}


def test_history_unknown_point_404():
    with TestClient(app) as c:
        assert c.get("/api/history/NOPE.P").status_code == 404


def test_split_sql_strips_comments():
    stmts = split_sql("-- a; comment\nCREATE TABLE a (x int);\n\nSELECT 1; -- trailing\n")
    assert stmts == ["CREATE TABLE a (x int)", "SELECT 1"]
