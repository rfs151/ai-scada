import type { Limits, Point, RtValue } from '../api/types'

/** IEC 104 品质描述位 */
export const Q = { OV: 0x01, BL: 0x10, SB: 0x20, NT: 0x40, IV: 0x80 } as const

const Q_LABEL: [number, string, string][] = [
  [Q.IV, '无效', 'iv'],
  [Q.NT, '非刷新', 'nt'],
  [Q.SB, '取代', 'sb'],
  [Q.BL, '闭锁', 'bl'],
  [Q.OV, '溢出', 'ov'],
]

export function qualityFlags(q: number): { label: string; cls: string }[] {
  return Q_LABEL.filter(([bit]) => q & bit).map(([, label, cls]) => ({ label, cls }))
}

/** 双点遥信：00 中间态 / 01 分 / 10 合 / 11 不定 */
export const DPI_TEXT = ['中间态', '分', '合', '不定'] as const

export function formatValue(p: Point, v: RtValue | undefined): string {
  if (!v) return '—'
  if (p.cat === 'YX') return p.double ? DPI_TEXT[v.v] ?? '?' : v.v ? '动作' : '复归'
  if (p.cat === 'DD') return Math.round(v.v).toLocaleString('zh-CN')
  return v.v.toFixed(p.decimals)
}

export type LimitState = 'hihi' | 'hi' | 'lo' | 'lolo' | null

export function limitState(limits: Limits | null, v: number | undefined): LimitState {
  if (!limits || v === undefined) return null
  if (limits.hihi !== undefined && v >= limits.hihi) return 'hihi'
  if (limits.hi !== undefined && v >= limits.hi) return 'hi'
  if (limits.lolo !== undefined && v <= limits.lolo) return 'lolo'
  if (limits.lo !== undefined && v <= limits.lo) return 'lo'
  return null
}

export const LIMIT_TEXT: Record<Exclude<LimitState, null>, string> = {
  hihi: '越上上限',
  hi: '越上限',
  lo: '越下限',
  lolo: '越下下限',
}

const pad = (n: number, w = 2) => String(n).padStart(w, '0')

/** 毫秒时标 → HH:MM:SS.mmm（本地时间） */
export function fmtTime(ms: number, withDate = false): string {
  const d = new Date(ms)
  const t = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`
  return withDate ? `${d.getMonth() + 1}-${pad(d.getDate())} ${t}` : t
}

export const COT_TEXT: Record<number, string> = { 3: '突发(SOE)', 20: '总召发现' }
