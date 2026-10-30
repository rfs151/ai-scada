/**
 * 图模一致性校验：接线图引用的设备、连接节点、点号必须在模型中存在，模型中的设备必须都上图。
 * 数据源为 config/station.yaml（模型）与 docs/点表.csv（由模型生成的点表）。
 */
import { describe, expect, it } from 'vitest'
import { parse } from 'yaml'
import stationYaml from '../../../config/station.yaml?raw'
import pointCsv from '../../../docs/点表.csv?raw'
import { layout } from './layout'

interface RawEq {
  id: string | number
  type: string
  nodes?: string[]
  node?: string
  from?: string
  to?: string
  hv?: string
  lv?: string
}
interface RawBay {
  id: string
  node?: string
  equipment?: RawEq[]
}

const raw = parse(stationYaml) as { grid: { node: string }; bays: RawBay[] }
const equipment = raw.bays.flatMap((b) => b.equipment ?? []).map((e) => ({ ...e, id: String(e.id) }))
const eqById = new Map(equipment.map((e) => [e.id, e]))

const modelNodes = new Set<string>([raw.grid.node])
for (const b of raw.bays) if (b.node) modelNodes.add(b.node)
for (const e of equipment) {
  for (const n of [...(e.nodes ?? []), e.node, e.from, e.to, e.hv, e.lv]) if (n) modelNodes.add(n)
}

const pointIds = new Set(
  pointCsv
    .trim()
    .split('\n')
    .slice(1)
    .map((line) => line.split(',')[3]),
)

describe('接线图 ↔ 模型', () => {
  it('所有开关、主变、线路都已上图且只画一次', () => {
    const drawn = [...layout.symbols.map((s) => s.eq), ...layout.lines.map((l) => l.eq)]
    expect(new Set(drawn).size).toBe(drawn.length)
    const missing = equipment.filter((e) => !drawn.includes(e.id)).map((e) => e.id)
    expect(missing).toEqual([])
  })

  it('图元类型与模型一致', () => {
    for (const s of layout.symbols) {
      const e = eqById.get(s.eq)
      expect(e, `设备 ${s.eq} 不在模型中`).toBeDefined()
      expect(e!.type).not.toBe('line')
    }
    for (const l of layout.lines) expect(eqById.get(l.eq)?.type, l.eq).toBe('line')
  })

  it('连接线与端子引用的节点都存在', () => {
    for (const n of [...layout.wires, ...layout.terms, ...layout.flows].map((w) => w.node)) {
      expect(modelNodes.has(n), `节点 ${n} 不在模型中`).toBe(true)
    }
  })

  it('量测标注与潮流箭头引用的点号都存在', () => {
    for (const pt of [...layout.meas, ...layout.flows].map((m) => m.pt)) {
      expect(pointIds.has(pt), `点号 ${pt} 不在点表中`).toBe(true)
    }
  })

  it('图元不超出画布', () => {
    for (const s of layout.symbols) {
      expect(s.x).toBeGreaterThan(0)
      expect(s.x).toBeLessThan(layout.width)
      expect(s.y).toBeGreaterThan(0)
      expect(s.y).toBeLessThan(layout.height)
    }
  })
})
