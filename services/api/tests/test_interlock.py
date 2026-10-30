import pytest

from app.control.interlock import Interlock, RuleSyntaxError, TagInfo, evaluate, parse_rule
from scadalib.iec104 import DPI
from scadalib.model import default_config_path, load_station


@pytest.fixture(scope="module")
def il():
    st = load_station(default_config_path())
    return Interlock.from_file(st, default_config_path().parent / "interlock.yaml")


def positions(**overrides):
    """正常运行方式下的开关位置，可按设备覆盖（键名前加 d 以便写数字编号，如 d101="分"）。"""
    st = load_station(default_config_path())
    pos = {e.id: (DPI.ON if e.normally_closed else DPI.OFF) for e in st.equipment if e.is_switch}
    for k, v in overrides.items():
        pos[k.removeprefix("d")] = {"合": DPI.ON, "分": DPI.OFF, "中": DPI.INDETERMINATE}[v]
    return pos.get


def test_parse_and_evaluate():
    tree = parse_rule("!(901=合 & 902=合) | 900=分")
    pos = {"901": DPI.ON, "902": DPI.ON, "900": DPI.ON}.get
    assert evaluate(tree, pos) is False
    assert evaluate(tree, {"901": DPI.ON, "902": DPI.OFF, "900": DPI.ON}.get) is True
    for bad in ("101=", "101=合 &", "(101=合", "101=开", "101=合 102=分"):
        with pytest.raises(RuleSyntaxError):
            parse_rule(bad)


def test_breaker_open_allowed(il):
    r = il.check("912", "open", positions())
    assert r.ok, r.violations


def test_disconnector_under_load_blocked(il):
    r = il.check("1016", "open", positions())
    assert not r.ok
    assert [v.category for v in r.violations] == ["规则库"]
    assert [(t.term, t.actual, t.ok) for t in r.terms] == [("101=分", "合", False)]
    assert il.check("1016", "open", positions(d101="分")).ok


def test_transformer_hv_disconnector_requires_full_outage(il):
    # 只断开 101：901 仍合位 → 闭锁，并逐项列出要求与实际
    r = il.check("1101", "open", positions(d101="分"))
    assert not r.ok
    assert [(t.term, t.actual, t.ok) for t in r.terms] == [
        ("901=分", "合", False),
        ("101=分", "分", True),
        ("100=分", "分", True),
    ]
    # 901、101 断开（100 正常即在分位）→ 主变已停电，允许拉开高压刀闸
    assert il.check("1101", "open", positions(d901="分", d101="分")).ok


def test_parallel_transformers_blocked(il):
    r = il.check("900", "close", positions())
    assert not r.ok and "主变并列" in r.violations[0].text
    assert il.check("900", "close", positions(d902="分")).ok


def test_state_and_tags(il):
    assert il.check("912", "close", positions()).violations[0].text == "912 已在合位"
    assert "位置中间态" in il.check("1016", "close", positions(d1016="中")).violations[0].text
    tags = [TagInfo("912", "maint")]
    assert any(v.category == "挂牌" for v in il.check("912", "open", positions(), tags).violations)
    assert il.check("912", "open", positions(), [TagInfo("912", "noclose")]).ok  # 禁止合闸牌不影响分闸
    assert not il.check("912", "close", positions(d912="分"), [TagInfo("912", "noclose")]).ok


def test_topology_close_onto_ground(il):
    # 912 分闸后在线路上挂接地线，再合 912 → 带地线合闸
    tags = [TagInfo("F02", "ground", "F02_LD")]
    r = il.check("912", "close", positions(d912="分"), tags)
    assert [v.category for v in r.violations] == ["拓扑"]
    assert "F02_O" in r.violations[0].text
    # 合接地刀同理
    r = il.check("912", "close", positions(d912="分", d9127="合"))
    assert not r.ok and r.violations[0].category == "拓扑"


def test_ground_wire_requires_dead_node(il):
    assert not il.check_ground("F02_LD", positions()).ok
    assert il.check_ground("F02_LD", positions(d912="分")).ok
