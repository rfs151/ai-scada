"""事故简报：用真实事故追忆数据（出线永久故障、主变差动）校验事实提取与规则定性。"""

import json
from pathlib import Path

import pytest

from app.brief import llm
from app.brief.facts import extract, render
from scadalib.model import default_config_path, load_station

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def st():
    return load_station(default_config_path())


def load(name):
    return json.loads((FIX / f"{name}.json").read_text())


def test_feeder_permanent_fault(st):
    f = extract(st, load("pdr_feeder_permanent"))
    assert f["classification"].startswith("线路永久性故障")
    assert [b["id"] for b in f["breakers"]] == ["912"] and f["breakers"][0]["to"] == "分"
    assert f["breakers"][0]["operations"] == 3  # 跳闸 → 重合 → 后加速再跳
    assert [x["bay"] for x in f["lost_feeders"]] == ["F02"] and f["lost_load_mw"] > 0
    assert f["fault_currents"][0]["point"] == "F02.I" and f["fault_currents"][0]["max_a"] > 3000
    # 事后的遥控合闸不算事故过程
    assert [s["state"] for s in f["later_operations"]] == ["合"]
    text = render(f)
    assert "带电巡线" in text and "+1.318 s" in text


def test_transformer_differential(st):
    f = extract(st, load("pdr_trafo_diff"))
    assert f["classification"].startswith("主变内部故障")
    moved = {b["id"]: (b["from"], b["to"]) for b in f["breakers"]}
    assert moved["901"] == ("合", "分") and moved["900"] == ("分", "合")  # 分段备自投转供
    assert f["lost_feeders"] == []
    assert "不得试送" in render(f)


def test_llm_unavailable_without_credentials(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert not llm.available()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert llm.available()
