"""遥测数据校核：利用量测冗余发现坏数据（简化的不良数据辨识）。

校核关系（同一时刻断面）：
    1. 功率平衡（基尔霍夫电流定律）
        10kV 母线：Σ 主变低压侧有功 = Σ 出线有功 − Σ 光伏送出（分段合位时两段合并）
        110kV：     进线一 + 进线二 = 1号主变 + 2号主变高压侧有功（内桥节点无损耗）
        主变：      高压侧有功 − 低压侧有功 = 损耗（< 3%）
    2. 间隔自洽：√(P² + Q²) ≈ √3 · U · I
    3. 方向合理：负荷出线有功不应为负（CT 极性接反的典型特征）

可疑测点定位：功率不平衡本身只说明"这组量测里有错"，结合间隔自洽 / 方向检查找出具体测点。
容差 = 各测点死区之和 + 相对误差：测点只在变化超过死区时上送，主站断面里的值最多偏离一个死区。
异常须持续 PERSIST_MS 才告警，消失同样持续才恢复，避免故障暂态（短路电流）误报。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from scadalib.iec104 import DPI, Quality
from scadalib.model import Station

from .rules import AlarmDraft

BAD = Quality.IV | Quality.NT
PERSIST_MS = 15_000
BAL_REL = 0.03
BAY_REL = 0.04
MIN_I_A = 10.0


@dataclass
class Issue:
    key: str
    text: str
    bay: str | None = None
    device: str | None = None
    points: list[str] = field(default_factory=list)


@dataclass
class _Track:
    issue: Issue
    first: int
    active: bool = False
    gone_since: int | None = None


class DataCheck:
    def __init__(self, st: Station):
        self.st = st
        self.bays = {b.id: b for b in st.bays}
        self.feeders = []  # (bay, 断路器, 母线节点, 是否光伏)
        for b in st.bays:
            if b.kind in ("feeder", "pv"):
                cb = next(e for e in b.equipment if e.type == "breaker")
                self.feeders.append((b, cb.id, cb.nodes[0], b.kind == "pv"))
        self.trafos = []  # (bay, 低压侧断路器, 母线节点)
        for b in st.bays:
            if b.kind == "transformer":
                cb = next(e for e in b.equipment if e.type == "breaker")
                self.trafos.append((b, cb.id, cb.nodes[1]))
        self.bus_bay = {b.attrs["node"]: b for b in st.bays if b.kind == "bus"}
        self.db = {p.id: p.deadband for p in st.points}
        self.tracks: dict[str, _Track] = {}
        self.snapshot: dict = {}

    # ------------------------------------------------------------ 断面计算

    def evaluate(self, vals: dict[str, tuple[float, int]], t: int) -> list[Issue]:
        def v(pid: str) -> float | None:
            x = vals.get(pid)
            return None if x is None or x[1] & BAD else float(x[0])

        def closed(cb: str) -> bool:
            return v(f"{cb}.POS") == DPI.ON

        issues: list[Issue] = []
        bays_out: list[dict] = []
        suspect: dict[str, Issue] = {}  # 间隔 → 该间隔自身的异常

        def bus_u(node: str) -> float | None:
            b = self.bus_bay.get(node)
            return v(f"{b.id}.UAB") if b else None

        # ---- 间隔自洽 + 方向
        bay_checks = [(b, cb, bus_u(node), "") for b, cb, node, _ in self.feeders]
        bay_checks += [(b, cb, bus_u(node), "L") for b, cb, node in self.trafos]
        bay_checks += [(b, None, v(f"{b.id}.UAB"), "") for b in self.st.bays if b.kind == "line_in"]
        for b, cb, u, pre in bay_checks:
            p, q, i = v(f"{b.id}.{pre}P"), v(f"{b.id}.{pre}Q"), v(f"{b.id}.{pre}I")
            if None in (p, q, i, u) or (cb and not closed(cb)) or i < MIN_I_A or u < 1:
                continue
            s_pq, s_ui = math.hypot(p, q), math.sqrt(3) * u * i / 1000
            dev = abs(s_pq - s_ui)
            d = self.db
            tol = (d[f"{b.id}.{pre}P"] + d[f"{b.id}.{pre}Q"] + BAY_REL * max(s_pq, s_ui)
                   + math.sqrt(3) * (u * d[f"{b.id}.{pre}I"] + i * 0.2) / 1000)  # fmt: skip
            ok = dev <= tol
            sign_ok = not (b.kind == "feeder" and p < -0.2)
            bays_out.append({"bay": b.id, "name": b.name, "s_pq": s_pq, "s_ui": s_ui,
                             "dev_pct": 100 * dev / max(s_ui, 1e-6), "ok": ok, "sign_ok": sign_ok})  # fmt: skip
            pts = [f"{b.id}.{pre}{m}" for m in ("P", "Q", "I")]
            if not ok:
                issue = Issue(f"bay:{b.id}", f"{b.name} 量测不自洽：√(P²+Q²) = {s_pq:.2f} MVA，√3·U·I = {s_ui:.2f} MVA，"
                              f"偏差 {100 * dev / max(s_ui, 1e-6):.0f}%（P、Q、I 中有错误量测）", b.id, cb, pts)  # fmt: skip
                issues.append(issue)
                suspect[b.id] = issue
            if not sign_ok:
                issue = Issue(f"sign:{b.id}", f"{b.name} 有功为 {p:.2f} MW，负荷线路不应向母线送电，疑似 CT 极性接反",
                              b.id, cb, pts[:2])  # fmt: skip
                issues.append(issue)
                suspect[b.id] = issue

        # ---- 10kV 母线功率平衡（分段合位时两段合并为一个节点）
        # 平衡项为 (间隔, 测点号, 符号)：光伏线路向母线送电，按流入计
        balances: list[dict] = []
        groups = [["BUS1", "BUS2"]] if closed("900") else [["BUS1"], ["BUS2"]]
        for g in groups:
            if all((bus_u(n) or 0) < 1 for n in g):
                continue
            ins = [(b, f"{b.id}.LP", 1) for b, cb, node in self.trafos if node in g and closed(cb)]
            outs = [
                (b, f"{b.id}.P", -1 if pv else 1)
                for b, cb, node, pv in self.feeders
                if node in g and closed(cb)
            ]
            name = "、".join(self.bus_bay[n].name for n in g)
            balances.append(self._balance(f"bal:{'+'.join(g)}", name, ins, outs, v, suspect, issues))

        # ---- 110kV 节点平衡（内桥节点无损耗）、主变两侧（损耗 < 3%，在相对容差内）
        lines = [(b, f"{b.id}.P", 1) for b in self.st.bays if b.kind == "line_in"]
        hv = [(b, f"{b.id}.HP", 1) for b, _, _ in self.trafos]
        balances.append(self._balance("bal:HV", "110kV 进线与主变高压侧", lines, hv, v, suspect, issues))
        for b, _, _ in self.trafos:
            balances.append(self._balance(f"bal:{b.id}", f"{b.name}高低压侧", [(b, f"{b.id}.HP", 1)],
                                          [(b, f"{b.id}.LP", 1)], v, suspect, issues))  # fmt: skip

        self.snapshot = {"t": t, "balances": [x for x in balances if x], "bays": bays_out}
        return issues

    def _balance(self, key, name, ins, outs, v, suspect, issues) -> dict | None:
        """ins / outs：(间隔, 测点号, 符号)。有无效量测（IV/NT）时不做该项校核。"""
        terms = ins + outs
        if any(v(pid) is None for _, pid, _ in terms):
            return None
        p_in = sum(sg * v(pid) for _, pid, sg in ins)
        p_out = sum(sg * v(pid) for _, pid, sg in outs)
        diff = p_in - p_out
        tol = sum(self.db[pid] for _, pid, _ in terms) + BAL_REL * max(abs(p_in), abs(p_out))
        ok = abs(diff) <= tol
        members = list(dict.fromkeys(b.id for b, _, _ in terms))
        if not ok:
            sus = [suspect[b] for b in members if b in suspect]
            hint = (
                ("；可疑间隔：" + "、".join(self.bays[i.bay].name for i in sus))
                if sus
                else "；未定位到可疑间隔（可能为变比错误）"
            )
            issues.append(Issue(key, f"{name}功率不平衡：流入 {p_in:.2f} MW，流出 {p_out:.2f} MW，差 {diff:+.2f} MW{hint}",
                                sus[0].bay if sus else None, sus[0].device if sus else None,
                                [p for i in sus for p in i.points]))  # fmt: skip
        return {"key": key, "name": name, "in": p_in, "out": p_out, "diff": diff, "tol": tol, "ok": ok,
                "members": members}  # fmt: skip

    # ------------------------------------------------------------ 持续判定 → 告警

    def run(self, vals: dict[str, tuple[float, int]], t: int) -> list[AlarmDraft]:
        current = {i.key: i for i in self.evaluate(vals, t)}
        out: list[AlarmDraft] = []
        for key, issue in current.items():
            tr = self.tracks.get(key)
            if tr is None:
                self.tracks[key] = tr = _Track(issue, t)
            tr.issue, tr.gone_since = issue, None
            if not tr.active and t - tr.first >= PERSIST_MS:
                tr.active = True
                out.append(AlarmDraft(tr.first, 2, "数据校核", issue.text, issue.points[0] if issue.points else None,
                                      issue.bay, issue.device))  # fmt: skip
        for key, tr in list(self.tracks.items()):
            if key in current:
                continue
            if not tr.active:
                del self.tracks[key]
                continue
            tr.gone_since = tr.gone_since or t
            if t - tr.gone_since >= PERSIST_MS:
                del self.tracks[key]
                i = tr.issue
                text = i.text.split("：")[0]
                out.append(AlarmDraft(t, 5, "数据校核恢复", f"{text} 恢复正常", i.points[0] if i.points else None,
                                      i.bay, i.device))  # fmt: skip
        self.snapshot["issues"] = [
            {
                "key": k,
                "text": tr.issue.text,
                "since": tr.first,
                "active": tr.active,
                "points": tr.issue.points,
            }
            for k, tr in self.tracks.items()
        ]
        return out
