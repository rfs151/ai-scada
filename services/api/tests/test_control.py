import pytest

from app.auth.security import User
from app.control.interlock import Interlock, TagInfo
from app.control.service import ControlError, ControlService
from scadalib.iec104 import DPI
from scadalib.model import default_config_path, load_station

ZHANG = User("zhang", "张工", "operator")
LI = User("li", "李工", "operator")
WANG = User("wang", "王工", "supervisor")
USERS = {("wang", "pw"): WANG, ("li", "pw"): LI, ("zhang", "pw"): ZHANG}


class FakeTransport:
    def __init__(self):
        self.calls: list[tuple] = []
        self.results: dict[str, dict] = {}

    async def send(self, point, action, value, timeout):
        self.calls.append((point, action, value))
        return self.results.get(action, {"ok": True, "stage": "actterm", "elapsed_ms": 5})


class Env:
    def __init__(self):
        self.st = load_station(default_config_path())
        self.il = Interlock.from_file(self.st, default_config_path().parent / "interlock.yaml")
        self.pos = {
            e.id: (DPI.ON if e.normally_closed else DPI.OFF) for e in self.st.equipment if e.is_switch
        }
        self.tags: list[TagInfo] = []
        self.audits: list[tuple] = []
        self.pushed: list[dict] = []
        self.commanded: dict[str, dict] = {}
        self.now = 1000.0
        self.tr = FakeTransport()

        async def positions():
            return dict(self.pos).get

        async def tags():
            return list(self.tags)

        async def authenticate(u, p):
            return USERS.get((u, p))

        async def audit(action, **kw):
            self.audits.append((action, kw.get("ok", True)))

        async def publish(m):
            self.pushed.append(m)

        async def mark(dev, info):
            self.commanded[dev] = info

        self.svc = ControlService(self.st, self.il, self.tr, positions=positions, tags=tags,
                                  authenticate=authenticate, audit=audit, publish=publish, mark_commanded=mark,
                                  clock=lambda: self.now)  # fmt: skip


@pytest.fixture
def env():
    return Env()


async def test_full_flow(env):
    s = await env.svc.create(ZHANG, "912.YK", "open", "912")
    assert s["state"] == "pending" and s["check"]["ok"]
    s = await env.svc.supervise(s["id"], ZHANG, "wang", "pw")
    assert s["state"] == "authorized" and s["supervisor"]["name"] == "王工"
    s = await env.svc.select(s["id"], ZHANG)
    assert s["state"] == "selected" and 0 < s["remaining_s"] <= 28
    s = await env.svc.execute(s["id"], ZHANG)
    assert s["state"] == "done"
    assert env.tr.calls == [("912.YK", "select", "open"), ("912.YK", "execute", "open")]
    assert env.commanded["912"]["operator"] == "张工" and env.commanded["912"]["supervisor"] == "王工"
    assert [a for a, _ in env.audits] == [
        "control.create",
        "control.supervise",
        "control.select",
        "control.execute",
    ]
    assert [m["session"]["state"] for m in env.pushed][-1] == "done"


async def test_confirm_must_match(env):
    with pytest.raises(ControlError) as e:
        await env.svc.create(ZHANG, "912.YK", "open", "911")
    assert e.value.status == 400 and env.audits[-1] == ("control.create", False)


async def test_interlock_rejects(env):
    s = await env.svc.create(ZHANG, "1016.YK", "open", "1016")
    assert s["state"] == "rejected" and "101=分" in s["steps"][-1]["detail"]
    assert env.tr.calls == []


async def test_supervisor_rules(env):
    s = await env.svc.create(ZHANG, "912.YK", "open", "912")
    for user, pw, msg in (("wang", "bad", "口令错误"), ("li", "pw", "不具备监护权限")):
        with pytest.raises(ControlError, match=msg):
            await env.svc.supervise(s["id"], ZHANG, user, pw)
    # 监护员自己发起时不能自己监护
    s2 = await env.svc.create(WANG, "911.YK", "open", "911")
    with pytest.raises(ControlError, match="不能是操作人本人"):
        await env.svc.supervise(s2["id"], WANG, "wang", "pw")
    # 别人不能接手他人的会话
    with pytest.raises(ControlError, match="只能由操作人本人"):
        await env.svc.supervise(s["id"], LI, "wang", "pw")


async def test_one_session_per_device(env):
    await env.svc.create(ZHANG, "912.YK", "open", "912")
    with pytest.raises(ControlError) as e:
        await env.svc.create(LI, "912.YK", "open", "912")
    assert e.value.status == 409


async def test_recheck_before_execute(env):
    s = await env.svc.create(ZHANG, "912.YK", "open", "912")
    await env.svc.supervise(s["id"], ZHANG, "wang", "pw")
    await env.svc.select(s["id"], ZHANG)
    env.tags.append(TagInfo("912", "maint"))  # 选择后有人挂了检修牌
    s = await env.svc.execute(s["id"], ZHANG)
    assert s["state"] == "rejected"
    assert env.tr.calls[-1] == ("912.YK", "cancel", "open")  # 撤销子站选择


async def test_rtu_negative_confirm(env):
    env.tr.results["select"] = {"ok": False, "stage": "actcon", "detail": "ACTIVATION_CON"}
    s = await env.svc.create(ZHANG, "912.YK", "open", "912")
    await env.svc.supervise(s["id"], ZHANG, "wang", "pw")
    s = await env.svc.select(s["id"], ZHANG)
    assert s["state"] == "failed" and "子站否定确认" in s["steps"][-1]["detail"]


async def test_select_window_expires(env):
    s = await env.svc.create(ZHANG, "912.YK", "open", "912")
    await env.svc.supervise(s["id"], ZHANG, "wang", "pw")
    await env.svc.select(s["id"], ZHANG)
    env.now += 29
    await env.svc.sweep()
    assert env.svc.get(s["id"]).state == "expired"
    with pytest.raises(ControlError, match="已超时"):
        await env.svc.execute(s["id"], ZHANG)


async def test_cancel_after_select(env):
    s = await env.svc.create(ZHANG, "912.YK", "open", "912")
    await env.svc.supervise(s["id"], ZHANG, "wang", "pw")
    await env.svc.select(s["id"], ZHANG)
    s = await env.svc.cancel(s["id"], ZHANG)
    assert s["state"] == "cancelled" and env.tr.calls[-1][1] == "cancel"


async def test_adjust(env):
    r = await env.svc.adjust(ZHANG, "T1.TAPCMD", "raise")
    assert r["ok"] and env.tr.calls[-1] == ("T1.TAPCMD", "execute", "raise")
    with pytest.raises(ControlError, match="超出范围"):
        await env.svc.adjust(ZHANG, "F04.PSET", 9.0)
    env.tags.append(TagInfo("T1", "maint"))
    with pytest.raises(ControlError, match="检修"):
        await env.svc.adjust(ZHANG, "T1.TAPCMD", "lower")
