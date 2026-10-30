import { describe, expect, it } from 'vitest'
import type { Point } from '../api/types'
import { formatValue, limitState, qualityFlags } from './format'

const yc: Point = {
  id: 'BUS1.UAB', name: '', cat: 'YC', ioa: 0x4001, bay: 'BUS1', ref: 'BUS1', meas: 'UAB', unit: 'kV',
  deadband: 0.02, limits: { hihi: 11, hi: 10.7, lo: 10.0, lolo: 9.3 }, double: false, target: null, decimals: 3,
}

describe('format', () => {
  it('formats analog with decimals and double-point positions', () => {
    expect(formatValue(yc, { v: 10.41234, q: 0, t: 0 })).toBe('10.412')
    const pos = { ...yc, cat: 'YX', double: true } as Point
    expect(formatValue(pos, { v: 2, q: 0, t: 0 })).toBe('合')
    expect(formatValue(pos, { v: 0, q: 0, t: 0 })).toBe('中间态')
  })

  it('detects four-level limits', () => {
    expect(limitState(yc.limits, 10.4)).toBeNull()
    expect(limitState(yc.limits, 10.75)).toBe('hi')
    expect(limitState(yc.limits, 11.2)).toBe('hihi')
    expect(limitState(yc.limits, 9.9)).toBe('lo')
    expect(limitState(yc.limits, 9.0)).toBe('lolo')
  })

  it('decodes quality bits', () => {
    expect(qualityFlags(0)).toEqual([])
    expect(qualityFlags(0xc0).map((f) => f.label)).toEqual(['无效', '非刷新'])
  })
})
