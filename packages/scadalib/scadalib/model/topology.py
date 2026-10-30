"""拓扑分析：连接节点 + 设备 + 开关状态 → 带电 / 接地范围。

与前端 web/src/lib/topology.ts 规则一致（前端用于着色，这里用于五防校验与告警）：
  - 带电：从电源点出发，经合位开关 / 线路 / 主变可达
  - 接地：从合位接地刀或临时接地线出发，经合位开关 / 线路可达（主变两侧电气隔离，不传递）
  - 带电又接地：冲突（带电合接地刀 / 带地线合闸）
  - 开关位置未知、中间态、不定态按"不导通"处理
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum

from .station import Equipment, Station

SWITCH_TYPES = {"breaker", "disconnector"}


class NodeState(StrEnum):
    ENERGIZED = "energized"
    DEAD = "dead"
    GROUNDED = "grounded"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class TopoResult:
    energized: frozenset[str]
    grounded: frozenset[str]

    def state(self, node: str) -> NodeState:
        en, gr = node in self.energized, node in self.grounded
        if en and gr:
            return NodeState.CONFLICT
        if gr:
            return NodeState.GROUNDED
        return NodeState.ENERGIZED if en else NodeState.DEAD

    @property
    def conflicts(self) -> frozenset[str]:
        return self.energized & self.grounded


def _bfs(starts: Iterable[str], adj: dict[str, list[str]]) -> frozenset[str]:
    seen = set(starts)
    q = deque(seen)
    while q:
        for m in adj[q.popleft()]:
            if m not in seen:
                seen.add(m)
                q.append(m)
    return frozenset(seen)


def analyze(
    equipment: Iterable[Equipment],
    sources: Iterable[str],
    is_closed: Callable[[str], bool],
    extra_grounds: Iterable[str] = (),
) -> TopoResult:
    """
    :param is_closed: 开关是否合位
    :param extra_grounds: 额外接地点（挂接地线的连接节点）
    """
    power: dict[str, list[str]] = defaultdict(list)
    earth: dict[str, list[str]] = defaultdict(list)
    grounded_at = list(extra_grounds)

    for e in equipment:
        nodes = e.nodes
        if e.type == "earth_switch":
            if is_closed(e.id):
                grounded_at.append(nodes[0])
            continue
        if len(nodes) != 2:
            continue
        conducting = is_closed(e.id) if e.type in SWITCH_TYPES else e.type in ("line", "transformer")
        if not conducting:
            continue
        a, b = nodes
        power[a].append(b)
        power[b].append(a)
        if e.type != "transformer":
            earth[a].append(b)
            earth[b].append(a)

    return TopoResult(_bfs(sources, power), _bfs(grounded_at, earth))


def analyze_station(
    st: Station, is_closed: Callable[[str], bool], extra_grounds: Iterable[str] = ()
) -> TopoResult:
    return analyze(st.equipment, [st.grid["node"]], is_closed, extra_grounds)
