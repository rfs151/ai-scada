"""事故简报：从事故追忆数据提取事实，按规则生成简报（纯计算，无 I/O）。

事实全部来自追忆记录（SOE、遥测变化），规则只做确定性的归纳：
    - 动作序列：设备时标相对触发时刻的毫秒数
    - 开关变位：窗口起止状态对比、变位次数
    - 影响：失电出线及事故前负荷、故障最大电流、母线最低电压
    - 定性：差动/重瓦斯 → 主变内部故障；重合后再跳 → 永久性故障；重合成功 → 瞬时性故障；
            只有接地告警 → 单相接地；备自投动作且无出线保护 → 电源侧失电
大模型撰写时只拿到这份事实，不接触原始数据，避免编造。
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from scadalib.model import Cat, Station

TZ = ZoneInfo("Asia/Shanghai")
PRE_MS = 1000  # 事故前断面取触发前 1 秒
PROT_MEAS = ("PROT", "DIFF", "GAS_H", "ACC", "RECL", "BZT", "GND")
SETTLE_MS = 2000  # 最后一个保护/自动装置信号后 2 秒视为事故过程结束，之后的变位算"后续操作"


def _state(double: bool, v: float) -> str:
    if double:
        return {1: "分", 2: "合"}.get(int(v), f"不定态({int(v)})")
    return "动作" if v else "复归"


def _clock(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def extract(st: Station, rep: dict) -> dict:
    trig = rep["pdr"]["ts"]
    pts = {p.id: p for p in st.points}

    def meas(pid: str) -> str | None:
        p = pts.get(pid)
        return p.meas if p else None

    def ev_t(e: dict) -> int:
        return e["dev_t"] or e["recv_t"]

    prot_ts = [ev_t(e) for e in rep["events"] if meas(e["p"]) in PROT_MEAS and ev_t(e) >= trig]
    end_t = (max(prot_ts) if prot_ts else trig) + SETTLE_MS

    # ---- 遥测：事故前断面、窗口内极值、事故后断面
    vals = {pid: v for pid, (v, _q) in rep["init"].items() if pid in pts and pts[pid].cat == Cat.YC}
    pre: dict[str, float] | None = None
    i_max: dict[str, tuple[float, int]] = {}
    u_min: dict[str, tuple[float, int]] = {}
    for t, pid, v, _q in rep["analog"]:
        if t > end_t:
            break
        if pre is None and t > trig - PRE_MS:
            pre = dict(vals)
        vals[pid] = v
        p = pts.get(pid)
        if p is None or t < trig - PRE_MS:
            continue
        if p.meas in ("I", "HI", "LI") and v > i_max.get(pid, (-1, 0))[0]:
            i_max[pid] = (v, t)
        if p.meas == "UAB" and p.bay.startswith("BUS") and v < u_min.get(pid, (1e9, 0))[0]:
            u_min[pid] = (v, t)
    pre = pre if pre is not None else dict(vals)
    post = vals

    # ---- 遥信：SOE 序列、开关变位
    pos0 = {pid: v for pid, (v, _q) in rep["init"].items() if pid.endswith(".POS")}
    pos = dict(pos0)
    ops: dict[str, int] = {}
    soe, later = [], []
    for e in rep["events"]:
        t = ev_t(e)
        item = {"dt_ms": t - trig, "name": e["name"], "state": _state(e["double"], e["v"]), "point": e["p"],
                "meas": meas(e["p"])}  # fmt: skip
        if t > end_t:
            if e["p"].endswith(".POS"):
                later.append(item)
            continue
        soe.append(item)
        if e["p"].endswith(".POS"):
            pos[e["p"]] = e["v"]
            ops[e["p"]] = ops.get(e["p"], 0) + 1

    breakers = []
    for pid, n in ops.items():
        eq = st.equip(pts[pid].ref)
        if eq.type != "breaker":
            continue
        breakers.append({"id": eq.id, "name": eq.name, "from": _state(True, pos0.get(pid, 0)),
                         "to": _state(True, pos[pid]), "operations": n})  # fmt: skip

    # ---- 影响
    lost = []
    for b in st.bays:
        if b.kind not in ("feeder", "pv"):
            continue
        cb = next(e for e in b.equipment if e.type == "breaker")
        if pos0.get(f"{cb.id}.POS") == 2 and pos.get(f"{cb.id}.POS") == 1:
            lost.append(
                {
                    "bay": b.id,
                    "name": b.name,
                    "breaker": cb.id,
                    "pre_p_mw": round(pre.get(f"{b.id}.P", 0.0), 2),
                }
            )
    currents = []
    for pid, (v, t) in sorted(i_max.items(), key=lambda x: -x[1][0]):
        base = pre.get(pid, 0.0)
        if v > max(1.5 * base, base + 50):
            currents.append({"point": pid, "name": pts[pid].name, "max_a": round(v, 1), "pre_a": round(base, 1),
                             "dt_ms": t - trig})  # fmt: skip
    voltages = []
    for pid, (v, t) in u_min.items():
        base = pre.get(pid, 0.0)
        if base > 0 and v < 0.9 * base:
            voltages.append({"point": pid, "name": pts[pid].name, "min_kv": round(v, 3), "pre_kv": round(base, 3),
                             "dt_ms": t - trig})  # fmt: skip
    trafo_load = {b.name: round(post.get(f"{b.id}.LOAD", 0.0), 1) for b in st.bays if b.kind == "transformer"}
    # 失电出线：开关由合到分，或开关合位但母线失压（主变跳闸、分段未合上）
    lost_ids = {x["bay"] for x in lost}
    for b in st.bays:
        if b.kind != "feeder" or b.id in lost_ids:
            continue
        cb = next(e for e in b.equipment if e.type == "breaker")
        bus = next(x for x in st.bays if x.kind == "bus" and x.attrs["node"] == cb.nodes[0])
        if (
            pos.get(f"{cb.id}.POS") == 2
            and post.get(f"{bus.id}.UAB", 10) < 1
            and pre.get(f"{bus.id}.UAB", 0) > 1
        ):
            lost.append({"bay": b.id, "name": b.name, "breaker": cb.id, "pre_p_mw": round(pre.get(f"{b.id}.P", 0.0), 2),
                         "reason": f"{bus.name}失压"})  # fmt: skip

    acted = {s["meas"] for s in soe if s["state"] == "动作"}
    reclosed_ok = "RECL" in acted and not lost
    if acted & {"DIFF", "GAS_H"}:
        kind = "主变内部故障，差动/重瓦斯保护动作跳开主变各侧"
    elif "PROT" in acted and "RECL" in acted and lost:
        kind = "线路永久性故障，重合闸重合于故障后再次跳闸"
    elif "PROT" in acted and reclosed_ok:
        kind = "线路瞬时性故障，重合闸重合成功"
    elif "PROT" in acted:
        kind = "线路故障，保护动作跳闸"
    elif "BZT" in acted:
        kind = "电源侧失电，备自投动作恢复供电"
    elif "GND" in acted:
        kind = "10kV 系统单相接地（小电流接地系统，不跳闸）"
    else:
        kind = "开关事故变位"

    return {
        "station": st.name,
        "title": rep["pdr"]["title"],
        "trigger_time": _clock(trig),
        "duration_s": round((end_t - SETTLE_MS - trig) / 1000, 3),
        "classification": kind,
        "soe": [s for s in soe if s["meas"] in PROT_MEAS or s["point"].endswith(".POS") or s["meas"] is None],
        "breakers": breakers,
        "lost_feeders": lost,
        "lost_load_mw": round(sum(x["pre_p_mw"] for x in lost), 2),
        "fault_currents": currents[:3],
        "voltage_dips": voltages,
        "transformer_load_pct_after": trafo_load,
        "later_operations": later,
    }


def _advice(f: dict) -> list[str]:
    k = f["classification"]
    loads = "、".join(f"{n} {v}%" for n, v in f["transformer_load_pct_after"].items())
    out = []
    if k.startswith("主变"):
        out += ["主变转检修前不得试送；检查瓦斯继电器气体、取油样做色谱分析，测试绕组直流电阻与绝缘",
                f"关注运行主变负载率（当前 {loads}），必要时转移或限制负荷"]  # fmt: skip
    elif k.startswith("线路永久"):
        names = "、".join(x["name"] for x in f["lost_feeders"]) or "故障线路"
        out += [f"通知运维单位对{names}带电巡线、查找故障点，故障隔离后经调度许可方可试送",
                "检查断路器机构及弹簧储能情况，记录本次重合闸与跳闸次数"]  # fmt: skip
    elif k.startswith("线路瞬时"):
        out += ["安排巡线检查瞬时故障原因（树障、异物、雷击等），记录重合闸动作次数"]
    elif k.startswith("电源侧"):
        out += ["联系上级调度确认对侧跳闸原因；备自投动作正确后注意运行方式变化，恢复后按调度令倒回正常方式"]
    elif k.startswith("10kV 系统单相接地"):
        out += ["按拉路顺序选线查找接地线路，单相接地运行时间一般不超过 2 小时"]
    out += ["核对保护及自动装置动作报告，复归信号、确认告警，填写事故记录并汇报调度"]
    return out


def render(f: dict) -> str:
    """规则模板简报（纯文本）。"""
    lines = [f"{f['station']}事故简报", "", "一、事故概况",
             f"{f['trigger_time']}，{f['title']}，动作过程持续 {f['duration_s']} 秒。初步判断：{f['classification']}。",
             "",
             "二、保护及自动装置动作情况（设备时标，相对触发时刻）"]  # fmt: skip
    for s in f["soe"]:
        lines.append(f"  {s['dt_ms'] / 1000:+.3f} s  {s['name']} {s['state']}")
    lines += ["", "三、事故影响"]
    for b in f["breakers"]:
        move = f"由{b['from']}到{b['to']}" if b["from"] != b["to"] else f"最终{b['to']}位"
        lines.append(f"  断路器 {b['id']}（{b['name']}）{move}，变位 {b['operations']} 次")
    if f["lost_feeders"]:
        names = "、".join(
            x["name"] + (f"（{x['reason']}）" if x.get("reason") else "") for x in f["lost_feeders"]
        )
        lines.append(f"  失电线路：{names}，损失负荷约 {f['lost_load_mw']} MW")
    else:
        lines.append("  无线路失电")
    for c in f["fault_currents"]:
        lines.append(f"  {c['name']}最大 {c['max_a']} A（事故前 {c['pre_a']} A）")
    for v in f["voltage_dips"]:
        lines.append(f"  {v['name']}最低 {v['min_kv']} kV（事故前 {v['pre_kv']} kV）")
    if f["later_operations"]:
        lines += ["", "（事故过程结束后的开关操作）"]
        lines += [f"  {s['dt_ms'] / 1000:+.3f} s  {s['name']} {s['state']}" for s in f["later_operations"]]
    lines += ["", "四、处置建议"] + [f"  {i}. {a}" for i, a in enumerate(_advice(f), 1)]
    return "\n".join(lines)
