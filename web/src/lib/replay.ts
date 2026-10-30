import type { PdrReplay, RtValue, YxEvent } from '../api/types'

/** 回放时间线上的一次变化 */
export interface Change {
  t: number
  pid: string
  v: number
  q: number
}

/** 事件时标：SOE 以设备时标为准，总召发现的变位只有接收时间 */
export const eventTime = (e: YxEvent) => e.dev_t ?? e.recv_t

/**
 * 事故追忆回放：起始断面 + 按时间排序的变化序列 → 任意时刻的全站断面。
 * 变化量通常只有几百条，按时刻全量重放即可，无需增量维护。
 */
export class Replay {
  readonly changes: Change[]

  constructor(readonly data: PdrReplay) {
    const yx: Change[] = data.events.map((e) => ({ t: eventTime(e), pid: e.p, v: e.v, q: 0 }))
    const an: Change[] = data.analog.map(([t, pid, v, q]) => ({ t, pid, v, q }))
    // 稳定排序：同一时刻保持原有先后（SOE 同毫秒多个信号的顺序有意义）
    this.changes = [...an, ...yx].sort((a, b) => a.t - b.t)
  }

  get start() {
    return this.data.start
  }
  get end() {
    return this.data.end
  }
  get trigger() {
    return this.data.pdr.ts
  }

  valuesAt(t: number): Record<string, RtValue> {
    const out: Record<string, RtValue> = {}
    for (const [pid, [v, q]] of Object.entries(this.data.init)) out[pid] = { v, q, t: this.start }
    for (const c of this.changes) {
      if (c.t > t) break
      out[c.pid] = { v: c.v, q: c.q, t: c.t }
    }
    return out
  }

  /** (t - windowMs, t] 内变位的设备，用于回放时闪烁提示 */
  movedDevices(t: number, windowMs = 1500): Set<string> {
    const s = new Set<string>()
    for (const e of this.data.events) {
      const et = eventTime(e)
      if (et > t - windowMs && et <= t && e.p.endsWith('.POS')) s.add(e.p.slice(0, -4))
    }
    return s
  }

  /** t 之后的下一个 / t 之前的上一个 SOE 时刻 */
  nextEvent(t: number): number | null {
    const e = this.data.events.find((x) => eventTime(x) > t + 0.5)
    return e ? eventTime(e) : null
  }
  prevEvent(t: number): number | null {
    const list = this.data.events.filter((x) => eventTime(x) < t - 0.5)
    return list.length ? eventTime(list[list.length - 1]) : null
  }
}
