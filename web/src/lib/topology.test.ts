import { describe, expect, it } from 'vitest'
import { analyzeTopology, type TopoEquipment } from './topology'

// 简化的内桥接线：两回进线 → 两台主变 → 两段 10kV 母线（分段开关 900）
const EQ: TopoEquipment[] = [
  { id: 'L1', type: 'line', nodes: ['GRID', 'L1_T'] },
  { id: '10167', type: 'earth_switch', nodes: ['L1_T'] },
  { id: '101', type: 'breaker', nodes: ['L1_T', 'HB1'] },
  { id: 'L2', type: 'line', nodes: ['GRID', 'L2_T'] },
  { id: '102', type: 'breaker', nodes: ['L2_T', 'HB2'] },
  { id: '100', type: 'breaker', nodes: ['HB1', 'HB2'] },
  { id: 'T1', type: 'transformer', nodes: ['HB1', 'T1_L'] },
  { id: '901', type: 'breaker', nodes: ['T1_L', 'BUS1'] },
  { id: 'T2', type: 'transformer', nodes: ['HB2', 'T2_L'] },
  { id: '902', type: 'breaker', nodes: ['T2_L', 'BUS2'] },
  { id: '900', type: 'breaker', nodes: ['BUS1', 'BUS2'] },
  { id: '911', type: 'breaker', nodes: ['BUS1', 'F01_O'] },
  { id: '9117', type: 'earth_switch', nodes: ['F01_O'] },
  { id: 'F01', type: 'line', nodes: ['F01_O', 'F01_LD'] },
]

const NORMAL_OPEN = new Set(['100', '900', '10167', '9117'])

function run(overrides: Record<string, boolean> = {}) {
  return analyzeTopology(EQ, ['GRID'], (id) => overrides[id] ?? !NORMAL_OPEN.has(id))
}

describe('analyzeTopology', () => {
  it('正常方式全站带电', () => {
    const t = run()
    for (const n of ['L1_T', 'HB1', 'T1_L', 'BUS1', 'BUS2', 'F01_LD']) expect(t.state(n)).toBe('energized')
    expect(t.grounded.size).toBe(0)
  })

  it('出线开关分闸：线路失电，母线仍带电', () => {
    const t = run({ '911': false })
    expect(t.state('F01_O')).toBe('dead')
    expect(t.state('F01_LD')).toBe('dead')
    expect(t.state('BUS1')).toBe('energized')
  })

  it('进线开关分闸，桥开关合上后由另一回进线供电', () => {
    expect(run({ '101': false }).state('BUS1')).toBe('dead')
    const t = run({ '101': false, '100': true })
    expect(t.state('HB1')).toBe('energized')
    expect(t.state('BUS1')).toBe('energized')
    // 线路由对侧电源充电，站内断路器分开后线路侧仍带电
    expect(t.state('L1_T')).toBe('energized')
  })

  it('主变低压开关分闸后，合分段开关由另一台主变带两段母线', () => {
    expect(run({ '901': false }).state('BUS1')).toBe('dead')
    expect(run({ '901': false, '900': true }).state('BUS1')).toBe('energized')
  })

  it('合接地刀：接地范围经合位开关、线路传递，不穿越主变', () => {
    const t = run({ '911': false, '9117': true })
    expect(t.state('F01_O')).toBe('grounded')
    expect(t.state('F01_LD')).toBe('grounded')
    expect(t.state('BUS1')).toBe('energized')

    // 110kV 侧全停后在出线合接地刀：接地经 911、901 到主变低压侧，但不到高压侧
    const g = run({ '101': false, '102': false, '9117': true })
    expect(g.state('BUS1')).toBe('grounded')
    expect(g.state('T1_L')).toBe('grounded')
    expect(g.state('HB1')).toBe('dead')
  })

  it('带电合接地刀标为冲突', () => {
    const t = run({ '9117': true })
    expect(t.state('F01_O')).toBe('conflict')
    expect(t.state('BUS1')).toBe('conflict')
  })

  it('开关位置未知按不导通处理', () => {
    const t = analyzeTopology(EQ, ['GRID'], () => false)
    expect(t.state('GRID')).toBe('energized')
    expect(t.state('L1_T')).toBe('energized')
    expect(t.state('HB1')).toBe('dead')
  })

  it('临时接地线参与拓扑', () => {
    const t = analyzeTopology(EQ, ['GRID'], (id) => !NORMAL_OPEN.has(id) && id !== '911', ['F01_LD'])
    expect(t.state('F01_O')).toBe('grounded')
    expect(t.state('BUS1')).toBe('energized')
  })
})
