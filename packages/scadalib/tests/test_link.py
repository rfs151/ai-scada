"""自研主站 ↔ 自研子站 集成测试（本机回环 TCP）。"""

import asyncio
from datetime import datetime

import pytest

from scadalib.iec104 import (
    COT,
    DPI,
    CmdCheck,
    Cycles,
    Iec104Client,
    Iec104Server,
    InfoObject,
    LinkParams,
    TypeID,
    apci,
)

from .conftest import free_port


class FakeRtu:
    def __init__(self):
        self.breaker = DPI.ON
        self.synced: datetime | None = None
        self.executed: list[tuple[TypeID, int, object]] = []

    def interrogation(self):
        return {
            TypeID.M_DP_NA_1: [InfoObject(1, self.breaker)],
            TypeID.M_ME_NC_1: [InfoObject(0x4001 + i, float(i)) for i in range(100)],
        }

    def counters(self):
        return [InfoObject(0x6401, 1000)]

    def check_command(self, type_id, obj):
        if obj.ioa == 0x6001:
            return CmdCheck.OK
        if obj.ioa == 0x6002:
            return CmdCheck.REJECT
        return CmdCheck.UNKNOWN_IOA

    def requires_select(self, type_id, ioa):
        return True

    async def execute_command(self, type_id, obj):
        self.executed.append((type_id, obj.ioa, obj.value))
        self.breaker = obj.value
        return True

    def clock_sync(self, t):
        self.synced = t


class Collector:
    def __init__(self):
        self.asdus = []
        self.states = []
        self.up = asyncio.Event()

    async def on_data(self, a):
        self.asdus.append(a)

    async def on_state(self, state, detail):
        self.states.append(state)
        if state == "up":
            self.up.set()
        else:
            self.up.clear()

    def objects(self, type_id, cot=None):
        return [
            o for a in self.asdus if a.type_id == type_id and (cot is None or a.cot == cot) for o in a.objects
        ]


@pytest.fixture
async def pair():
    rtu = FakeRtu()
    server = Iec104Server(rtu, host="127.0.0.1", port=free_port())
    await server.start()
    col = Collector()
    client = Iec104Client(
        "127.0.0.1",
        server.bound_port,
        on_data=col.on_data,
        on_state=col.on_state,
        reconnect_s=0.2,
    )
    task = asyncio.create_task(client.run_forever())
    await asyncio.wait_for(col.up.wait(), 5)
    # 启动流程（对时、总召、电度召唤）完成
    for _ in range(50):
        if col.objects(TypeID.M_IT_NA_1):
            break
        await asyncio.sleep(0.05)
    yield rtu, server, client, col
    client.stop()
    await server.stop()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_startup_sequence(pair):
    rtu, server, client, col = pair
    assert rtu.synced is not None
    assert len(col.objects(TypeID.M_ME_NC_1, COT.INTERROGATED_BY_STATION)) == 100  # 分多帧上送
    assert col.objects(TypeID.M_DP_NA_1)[0].value == DPI.ON
    assert col.objects(TypeID.M_IT_NA_1)[0].value == 1000
    assert col.objects(TypeID.M_EI_NA_1)  # 初始化结束


async def test_spontaneous_publish(pair):
    _, server, _, col = pair
    await server.publish(TypeID.M_ME_NC_1, [InfoObject(0x4001, 99.5)])
    await asyncio.sleep(0.1)
    assert col.objects(TypeID.M_ME_NC_1, COT.SPONTANEOUS)[-1].value == 99.5


async def test_select_before_operate(pair):
    rtu, _, client, _ = pair
    # 未选择直接执行 → 否定确认
    r = await client.command(TypeID.C_DC_NA_1, 0x6001, DPI.OFF, select=False)
    assert not r.ok and r.stage == "actcon"
    assert rtu.executed == []
    # 选择 → 执行 → 激活终止
    assert (await client.command(TypeID.C_DC_NA_1, 0x6001, DPI.OFF, select=True)).ok
    r = await client.command(TypeID.C_DC_NA_1, 0x6001, DPI.OFF, select=False)
    assert r.ok and r.stage == "actterm"
    assert rtu.executed == [(TypeID.C_DC_NA_1, 0x6001, DPI.OFF)]


async def test_select_value_mismatch_and_cancel(pair):
    rtu, _, client, _ = pair
    assert (await client.command(TypeID.C_DC_NA_1, 0x6001, DPI.OFF, select=True)).ok
    # 选择"分"却执行"合" → 拒绝
    assert not (await client.command(TypeID.C_DC_NA_1, 0x6001, DPI.ON, select=False)).ok
    assert (await client.command(TypeID.C_DC_NA_1, 0x6001, DPI.OFF, select=True)).ok
    assert await client.cancel_select(TypeID.C_DC_NA_1, 0x6001, DPI.OFF)
    assert not (await client.command(TypeID.C_DC_NA_1, 0x6001, DPI.OFF, select=False)).ok
    assert rtu.executed == []


async def test_command_rejections(pair):
    _, _, client, _ = pair
    r = await client.command(TypeID.C_DC_NA_1, 0x6002, DPI.OFF, select=True)
    assert not r.ok  # 闭锁
    r = await client.command(TypeID.C_DC_NA_1, 0x6FFF, DPI.OFF, select=True)
    assert not r.ok  # 未知信息体地址


async def test_flow_control_window(pair):
    """连续上送远超 k 个 I 帧：发送方必须等待确认，不丢帧不乱序。"""
    _, server, _, col = pair
    objs = [InfoObject(0x4001, float(i)) for i in range(300)]
    for o in objs:
        await server.publish(TypeID.M_ME_NC_1, [o])
    await asyncio.sleep(0.5)
    got = [o.value for o in col.objects(TypeID.M_ME_NC_1, COT.SPONTANEOUS)]
    assert got == [float(i) for i in range(300)]
    link = next(iter(server.links))
    assert link.unacked <= link.params.k


async def test_reconnect_after_server_restart(pair):
    rtu, server, client, col = pair
    port = server.bound_port
    await server.stop()
    await asyncio.sleep(0.3)
    assert client.state != "up"
    server2 = Iec104Server(rtu, host="127.0.0.1", port=port)
    await server2.start()
    await asyncio.wait_for(col.up.wait(), 5)
    await server2.stop()


async def test_t3_testfr_and_t1_timeout():
    """空闲超过 t3 发送 TESTFR；对端不回应时 t1 超时断开。"""
    frames = []
    params = LinkParams(t1=0.6, t2=0.3, t3=0.3)

    async def silent_peer(reader, writer):
        # 只完成 STARTDT，之后不再回应任何报文
        await apci.read_apdu(reader)
        writer.write(apci.encode_u(apci.UFunc.STARTDT_CON))
        while await reader.read(1024):
            pass
        writer.close()

    srv = await asyncio.start_server(silent_peer, "127.0.0.1", free_port())
    port = srv.sockets[0].getsockname()[1]
    col = Collector()
    client = Iec104Client(
        "127.0.0.1", port, on_data=col.on_data, on_state=col.on_state, params=params,
        cycles=Cycles(), on_frame=lambda d, raw: frames.append((d, raw)), reconnect_s=10,
    )  # fmt: skip
    task = asyncio.create_task(client.run_forever())
    await asyncio.sleep(2.0)
    tx_u = [apci.decode(r).ufunc for d, r in frames if d == "tx" and r[2] & 0x03 == 0x03]
    assert apci.UFunc.STARTDT_ACT in tx_u
    # 对时命令未被确认 → t1 超时断链
    assert "down" in col.states
    client.stop()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    srv.close()
    await srv.wait_closed()
