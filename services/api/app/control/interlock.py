"""五防（防误闭锁）校验。

三类规则依次校验，全部列出不合格项（而不是遇到第一条就返回），便于操作员一次看清原因：
  1. 状态与挂牌：设备位置不定 / 已在目标位置 / 检修牌 / 禁止合闸牌
  2. 规则库：config/interlock.yaml 中的逻辑式，逐项给出"要求 vs 实际"
  3. 拓扑防误：模拟操作后做拓扑分析，出现带电又接地的节点即闭锁（带地线合闸）；
     挂接地线前目标节点须不带电（带电挂地线）
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from scadalib.iec104 import DPI
from scadalib.model import NodeState, Station, analyze_station

POS_CN = {DPI.OFF: "分", DPI.ON: "合", DPI.INDETERMINATE: "中间态", DPI.INVALID: "不定"}
WORD_DPI = {"合": DPI.ON, "分": DPI.OFF}


# ============================================================== 规则表达式


class RuleSyntaxError(ValueError):
    pass


_TOKEN = re.compile(r"\s*(?:([()&|!])|([0-9A-Za-z_]+)\s*=\s*(合|分))")


@dataclass(frozen=True)
class Term:
    device: str
    want: DPI

    def __str__(self) -> str:
        return f"{self.device}={POS_CN[self.want]}"


@dataclass(frozen=True)
class Op:
    op: str  # & | !
    args: tuple


def parse_rule(text: str):
    """递归下降：expr := and ('|' and)* ; and := unary ('&' unary)* ; unary := '!' unary | '(' expr ')' | term"""
    tokens: list[str | Term] = []
    pos = 0
    text = text.strip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise RuleSyntaxError(f"无法解析：{text[pos:]!r}")
        tokens.append(m.group(1) if m.group(1) else Term(m.group(2), WORD_DPI[m.group(3)]))
        pos = m.end()
        while pos < len(text) and text[pos].isspace():
            pos += 1

    i = 0

    def peek():
        return tokens[i] if i < len(tokens) else None

    def take(expected: str | None = None):
        nonlocal i
        t = peek()
        if t is None or (expected is not None and t != expected):
            raise RuleSyntaxError(f"表达式不完整：{text!r}")
        i += 1
        return t

    def expr():
        args = [conj()]
        while peek() == "|":
            take("|")
            args.append(conj())
        return args[0] if len(args) == 1 else Op("|", tuple(args))

    def conj():
        args = [unary()]
        while peek() == "&":
            take("&")
            args.append(unary())
        return args[0] if len(args) == 1 else Op("&", tuple(args))

    def unary():
        t = peek()
        if t == "!":
            take("!")
            return Op("!", (unary(),))
        if t == "(":
            take("(")
            e = expr()
            take(")")
            return e
        if isinstance(t, Term):
            take()
            return t
        raise RuleSyntaxError(f"表达式错误：{text!r}")

    tree = expr()
    if i != len(tokens):
        raise RuleSyntaxError(f"多余内容：{text!r}")
    return tree


def evaluate(tree, pos: Callable[[str], int | None]) -> bool:
    if isinstance(tree, Term):
        return pos(tree.device) == tree.want
    if tree.op == "!":
        return not evaluate(tree.args[0], pos)
    vals = (evaluate(a, pos) for a in tree.args)
    return all(vals) if tree.op == "&" else any(vals)


def terms_of(tree) -> list[Term]:
    if isinstance(tree, Term):
        return [tree]
    return [t for a in tree.args for t in terms_of(a)]


# ============================================================== 校验


@dataclass
class TermCheck:
    term: str
    actual: str
    ok: bool


@dataclass
class Violation:
    category: str  # 状态 / 挂牌 / 规则库 / 拓扑
    text: str


@dataclass
class CheckResult:
    ok: bool
    violations: list[Violation] = field(default_factory=list)
    rule: str | None = None
    rule_desc: str | None = None
    terms: list[TermCheck] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class TagInfo:
    device: str
    kind: str  # maint 检修 / noclose 禁止合闸 / ground 接地线
    node: str | None = None


class Interlock:
    def __init__(self, st: Station, rules: dict[str, dict]):
        self.st = st
        self.rules: dict[str, dict] = {}
        for dev, r in rules.items():
            dev = str(dev)
            self.st.equip(dev)  # 规则引用的设备必须存在（StopIteration 即配置错误）
            compiled = {}
            for key in ("open", "close", "both"):
                if key in r:
                    tree = parse_rule(r[key])
                    for t in terms_of(tree):
                        self.st.equip(t.device)
                    compiled[key] = (r[key], tree)
            self.rules[dev] = {"desc": r.get("desc", ""), **compiled}

    @classmethod
    def from_file(cls, st: Station, path: Path) -> Interlock:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(st, raw.get("rules") or {})

    def check(
        self,
        device: str,
        action: str,
        pos: Callable[[str], int | None],
        tags: Iterable[TagInfo] = (),
    ) -> CheckResult:
        """
        :param action: open / close
        :param pos: 设备编号 → 双点遥信值（未知或品质无效返回 None）
        """
        tags = list(tags)
        res = CheckResult(ok=True)
        want = DPI.ON if action == "close" else DPI.OFF
        cur = pos(device)

        # ---- 1. 状态与挂牌
        if cur not in (DPI.ON, DPI.OFF):
            text = "位置未知或品质无效" if cur is None else f"位置{POS_CN[DPI(cur)]}"
            res.violations.append(Violation("状态", f"{device} {text}，禁止操作"))
        elif cur == want:
            res.violations.append(Violation("状态", f"{device} 已在{POS_CN[want]}位"))
        for t in tags:
            if t.device != device:
                continue
            if t.kind == "maint":
                res.violations.append(Violation("挂牌", f"{device} 挂检修牌，禁止遥控"))
            elif t.kind == "noclose" and action == "close":
                res.violations.append(Violation("挂牌", f"{device} 挂禁止合闸牌"))

        # ---- 2. 规则库
        r = self.rules.get(device)
        if r:
            text_tree = r.get(action) or r.get("both")
            if text_tree:
                text, tree = text_tree
                res.rule, res.rule_desc = text, r["desc"]
                for t in terms_of(tree):
                    v = pos(t.device)
                    res.terms.append(TermCheck(str(t), POS_CN.get(DPI(v), "未知") if v is not None else "未知",
                                               v == t.want))  # fmt: skip
                if not evaluate(tree, pos):
                    res.violations.append(Violation("规则库", f"不满足 {text}：{r['desc']}"))

        # ---- 3. 拓扑防误（仅合闸：分闸不会引入新的带电接地）
        if action == "close" and self.st.equip(device).type in ("breaker", "disconnector"):
            grounds = [t.node for t in tags if t.kind == "ground" and t.node]

            def closed_now(eid: str) -> bool:
                return pos(eid) == DPI.ON

            before = analyze_station(self.st, closed_now, grounds)
            after = analyze_station(self.st, lambda eid: eid == device or closed_now(eid), grounds)
            new = sorted(after.conflicts - before.conflicts)
            if new:
                res.violations.append(
                    Violation("拓扑", f"合闸后 {', '.join(new)} 将带电且接地（带地线合闸）")
                )

        res.ok = not res.violations
        return res

    def check_ground(
        self, node: str, pos: Callable[[str], int | None], tags: Iterable[TagInfo] = ()
    ) -> CheckResult:
        """挂接地线前校验：目标节点必须不带电（验电）。"""
        grounds = [t.node for t in tags if t.kind == "ground" and t.node]
        topo = analyze_station(self.st, lambda eid: pos(eid) == DPI.ON, grounds)
        st = topo.state(node)
        if st in (NodeState.ENERGIZED, NodeState.CONFLICT):
            return CheckResult(False, [Violation("拓扑", f"{node} 带电，禁止挂接地线（带电挂地线）")])
        return CheckResult(True)
