/**
 * 拓扑分析：由"连接节点 + 设备 + 开关实时状态"计算带电/接地范围，供接线图着色。
 *
 * 模型按 IEC 61970 CIM 的 ConnectivityNode 思路组织：每台设备连接 1~2 个节点，
 * 合位开关、线路、主变把节点连通；从电源点做广度优先搜索得到带电范围。
 *  - 带电：从电源点出发，经合位开关 / 线路 / 主变可达
 *  - 接地：从合位接地刀或临时接地线出发，经合位开关 / 线路可达（不穿越主变）
 *  - 带电又接地：带电合接地刀，属于恶性误操作，单独标出
 * 开关位置未知、中间态（00）或不定态（11）按"不导通"处理，宁可少着色也不误着色。
 */

export interface TopoEquipment {
  id: string
  type: string
  nodes: string[]
}

export const DPI_CLOSED = 2

export type NodeState = 'energized' | 'dead' | 'grounded' | 'conflict'

export interface TopoResult {
  energized: Set<string>
  grounded: Set<string>
  /** 节点着色状态 */
  state(node: string): NodeState
}

const SWITCH_TYPES = new Set(['breaker', 'disconnector'])

function bfs(starts: Iterable<string>, adj: Map<string, string[]>): Set<string> {
  const seen = new Set<string>()
  const queue = [...starts]
  for (const s of queue) seen.add(s)
  while (queue.length) {
    const n = queue.shift()!
    for (const m of adj.get(n) ?? []) {
      if (!seen.has(m)) {
        seen.add(m)
        queue.push(m)
      }
    }
  }
  return seen
}

/**
 * @param equipment 全部设备
 * @param sources 电源点连接节点
 * @param isClosed 开关是否在合位（按双点遥信 DPI === 2 判断）
 */
export function analyzeTopology(
  equipment: TopoEquipment[],
  sources: string[],
  isClosed: (id: string) => boolean,
  /** 临时接地线挂接的连接节点 */
  extraGrounds: string[] = [],
): TopoResult {
  const power = new Map<string, string[]>()
  const earth = new Map<string, string[]>()
  const link = (adj: Map<string, string[]>, a: string, b: string) => {
    if (!adj.has(a)) adj.set(a, [])
    if (!adj.has(b)) adj.set(b, [])
    adj.get(a)!.push(b)
    adj.get(b)!.push(a)
  }
  const earthed: string[] = [...extraGrounds]

  for (const e of equipment) {
    if (e.type === 'earth_switch') {
      if (isClosed(e.id)) earthed.push(e.nodes[0])
      continue
    }
    if (e.nodes.length !== 2) continue
    const [a, b] = e.nodes
    const conducting = SWITCH_TYPES.has(e.type) ? isClosed(e.id) : e.type === 'line' || e.type === 'transformer'
    if (!conducting) continue
    link(power, a, b)
    // 接地经开关、线路传递，但主变两侧绕组电气隔离
    if (e.type !== 'transformer') link(earth, a, b)
  }

  const energized = bfs(sources, power)
  const grounded = bfs(earthed, earth)

  return {
    energized,
    grounded,
    state(node) {
      const en = energized.has(node)
      const gr = grounded.has(node)
      if (en && gr) return 'conflict'
      if (gr) return 'grounded'
      return en ? 'energized' : 'dead'
    },
  }
}
