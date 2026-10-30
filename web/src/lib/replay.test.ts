import { describe, expect, it } from 'vitest'
import type { PdrReplay } from '../api/types'
import { Replay } from './replay'

const data: PdrReplay = {
  pdr: { id: 1, ts: 10_000, title: '保护动作', bay: 'F02', pre_s: 10, post_s: 10 },
  start: 0,
  end: 20_000,
  init: { '912.POS': [2, 0], 'F02.I': [300, 0], 'F02.PROT': [0, 0] },
  analog: [
    [9_800, 'F02.I', 3800, 0],
    [10_100, 'F02.I', 0, 0],
  ],
  events: [
    { p: 'F02.PROT', v: 1, old: 0, dev_t: 10_000, recv_t: 10_020, cot: 3 },
    { p: '912.POS', v: 1, old: 2, dev_t: 10_060, recv_t: 10_080, cot: 3 },
    { p: '912.POS', v: 2, old: 1, dev_t: 11_060, recv_t: 11_070, cot: 3 },
  ],
}

describe('Replay', () => {
  const r = new Replay(data)

  it('起始断面', () => {
    const v = r.valuesAt(0)
    expect(v['912.POS'].v).toBe(2)
    expect(v['F02.I'].v).toBe(300)
  })

  it('按设备时标逐条应用变化', () => {
    expect(r.valuesAt(9_900)['F02.I'].v).toBe(3800)
    const v = r.valuesAt(10_060)
    expect(v['F02.PROT'].v).toBe(1)
    expect(v['912.POS'].v).toBe(1)
    expect(v['F02.I'].v).toBe(3800)
    expect(r.valuesAt(12_000)['912.POS'].v).toBe(2)
  })

  it('变位设备与事件跳转', () => {
    expect([...r.movedDevices(10_500)]).toEqual(['912'])
    expect(r.movedDevices(13_000).size).toBe(0)
    expect(r.nextEvent(10_000)).toBe(10_060)
    expect(r.prevEvent(10_060)).toBe(10_000)
    expect(r.nextEvent(11_060)).toBeNull()
  })
})
