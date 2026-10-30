"""与 lib60870-C（c104 Python 绑定）的互操作测试：
1. c104 主站 → 自研子站：总召、对时、SBO 遥控
2. 自研主站 → c104 子站：总召、突发上送、SBO 遥控
"""

import asyncio
import time

import pytest

from scadalib.iec104 import COT, DPI, CmdCheck, Iec104Client, Iec104Server, InfoObject, TypeID

from .conftest import free_port

c104 = pytest.importorskip("c104")


def _wait(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


class Rtu:
    def __init__(self):
        self.breaker = DPI.ON
        self.synced = None
        self.executed = []

    def interrogation(self):
        return {
            TypeID.M_DP_NA_1: [InfoObject(1, self.breaker)],
            TypeID.M_ME_NC_1: [InfoObject(0x4001, 10.5), InfoObject(0x4002, 115.2)],
        }

    def counters(self):
        return [InfoObject(0x6401, 4242)]

    def check_command(self, type_id, obj):
        return CmdCheck.OK if obj.ioa == 0x6001 else CmdCheck.UNKNOWN_IOA

    def requires_select(self, type_id, ioa):
        return True

    async def execute_command(self, type_id, obj):
        self.executed.append(obj.value)
        return True

    def clock_sync(self, t):
        self.synced = t


async def test_c104_master_against_our_server():
    rtu = Rtu()
    port = free_port()
    server = Iec104Server(rtu, host="127.0.0.1", port=port)
    await server.start()

    client = c104.Client()
    conn = client.add_connection(ip="127.0.0.1", port=port, init=c104.Init.ALL)
    st = conn.add_station(common_address=1)
    p_cb = st.add_point(io_address=1, type=c104.Type.M_DP_NA_1)
    p_u = st.add_point(io_address=0x4002, type=c104.Type.M_ME_NC_1)
    cmd = st.add_point(io_address=0x6001, type=c104.Type.C_DC_NA_1)
    cmd.command_mode = c104.CommandMode.SELECT_AND_EXECUTE

    def scenario():
        client.start()
        assert _wait(lambda: conn.is_connected)
        assert _wait(lambda: p_u.value is not None and abs(p_u.value - 115.2) < 1e-3), "GI data not received"
        assert p_cb.value == c104.Double.ON
        assert _wait(lambda: rtu.synced is not None), "clock sync not received"
        cmd.value = c104.Double.OFF
        ok = cmd.transmit(cause=c104.Cot.ACTIVATION)  # c104 自动执行 选择 → 执行
        client.stop()
        return ok

    ok = await asyncio.to_thread(scenario)
    await server.stop()
    assert ok
    assert rtu.executed == [DPI.OFF]


async def test_our_master_against_c104_server():
    port = free_port()
    server = c104.Server(ip="127.0.0.1", port=port)
    st = server.add_station(common_address=1)
    p_u = st.add_point(io_address=0x4001, type=c104.Type.M_ME_NC_1)
    p_u.value = 12.5
    p_cb = st.add_point(io_address=1, type=c104.Type.M_DP_TB_1)
    p_cb.value = c104.Double.ON
    cmd = st.add_point(io_address=0x6001, type=c104.Type.C_DC_NA_1)
    cmd.command_mode = c104.CommandMode.SELECT_AND_EXECUTE
    received_cmds = []

    def on_cmd(
        point: c104.Point, previous_info: c104.Information, message: c104.IncomingMessage
    ) -> c104.ResponseState:
        received_cmds.append((message.is_select_command, point.value))
        return c104.ResponseState.SUCCESS

    cmd.on_receive(callable=on_cmd)
    server.start()

    data = []
    up = asyncio.Event()

    async def on_data(a):
        data.append(a)

    async def on_state(s, _):
        if s == "up":
            up.set()

    client = Iec104Client("127.0.0.1", port, on_data=on_data, on_state=on_state)
    task = asyncio.create_task(client.run_forever())
    try:
        await asyncio.wait_for(up.wait(), 5)
        for _ in range(50):
            if any(a.cot == COT.INTERROGATED_BY_STATION for a in data):
                break
            await asyncio.sleep(0.05)
        gi = {o.ioa: o.value for a in data if a.cot == COT.INTERROGATED_BY_STATION for o in a.objects}
        assert gi[0x4001] == pytest.approx(12.5)
        assert gi[1] == DPI.ON

        # c104 子站突发上送
        p_u.value = 13.75
        await asyncio.to_thread(p_u.transmit, cause=c104.Cot.SPONTANEOUS)
        for _ in range(50):
            spont = [o for a in data if a.cot == COT.SPONTANEOUS for o in a.objects]
            if spont:
                break
            await asyncio.sleep(0.05)
        assert spont[-1].value == pytest.approx(13.75)

        # SBO 遥控：c104 在选择-执行模式下拒绝未经选择的执行，并在内部消化选择帧，只对执行回调
        r = await client.command(TypeID.C_DC_NA_1, 0x6001, DPI.OFF, select=False, timeout=3)
        assert not r.ok, "execute without select must be rejected"
        assert (await client.command(TypeID.C_DC_NA_1, 0x6001, DPI.OFF, select=True)).ok
        r = await client.command(TypeID.C_DC_NA_1, 0x6001, DPI.OFF, select=False, timeout=3)
        assert r.ok, r
        assert received_cmds == [(False, c104.Double.OFF)]
    finally:
        client.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        server.stop()
