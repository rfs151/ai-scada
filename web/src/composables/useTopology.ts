import { computed } from 'vue'
import type { Equipment, RtValue } from '../api/types'
import { analyzeTopology, DPI_CLOSED, type NodeState } from '../lib/topology'
import { useModel } from '../stores/model'

const VL_COLOR: Record<string, string> = { HV: 'var(--kv110)', LV: 'var(--kv10)' }
const STATE_COLOR: Record<Exclude<NodeState, 'energized'>, string> = {
  dead: 'var(--deenergized)',
  grounded: 'var(--grounded)',
  conflict: 'var(--alarm)',
}

/**
 * 拓扑着色：由数据源（实时值或追忆回放值）中的开关位置计算带电/接地范围。
 * 实时画面与事故追忆共用，保证回放看到的着色与当时一致。
 */
export function useTopology(values: () => Record<string, RtValue>, grounds: () => string[] = () => []) {
  const model = useModel()

  const equipment = computed(() => {
    const m = new Map<string, Equipment>()
    for (const b of model.station?.bays ?? []) for (const e of b.equipment) m.set(e.id, e)
    return m
  })

  const posOf = (eq: string) => values()[`${eq}.POS`]

  /** 任一开关变位即重新计算（规模小，全量 BFS 即可） */
  const topo = computed(() => {
    const st = model.station
    if (!st) return null
    return analyzeTopology([...equipment.value.values()], st.sources, (id) => posOf(id)?.v === DPI_CLOSED, grounds())
  })

  function nodeState(node: string): NodeState {
    return topo.value?.state(node) ?? 'dead'
  }
  function nodeColor(node: string): string {
    const s = nodeState(node)
    return s === 'energized' ? (VL_COLOR[model.station?.nodes[node] ?? ''] ?? 'var(--text)') : STATE_COLOR[s]
  }

  /** 全站概况 */
  const kpi = computed(() => {
    const st = model.station
    const v = values()
    const val = (pid: string) => v[pid]?.v ?? 0
    const outlets = (st?.bays ?? []).filter((b) => b.kind === 'feeder' || b.kind === 'pv')
    return {
      import: val('L1.P') + val('L2.P'),
      load: (st?.bays ?? []).filter((b) => b.kind === 'feeder').reduce((s, b) => s + val(`${b.id}.P`), 0),
      pv: val('F04.P'),
      dead: outlets.filter((b) => nodeState(`${b.id}_O`) !== 'energized'),
    }
  })

  return { equipment, posOf, topo, nodeState, nodeColor, kpi }
}
