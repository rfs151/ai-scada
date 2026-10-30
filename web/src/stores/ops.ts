import { defineStore } from 'pinia'
import { computed, reactive, ref, shallowRef } from 'vue'
import { api } from '../api/client'
import type { Alarm, ControlSession, Tag } from '../api/types'
import { alarmSound } from '../lib/sound'
import { useRealtime } from './realtime'

const MAX_UNACKED = 500
const MAX_RECENT = 200
const MUTE_KEY = 'scada.mute'

export type BusMsg =
  | { type: 'alarm'; alarm: Alarm }
  | { type: 'alarm_ack'; ids: number[]; by: string; ts: number }
  | { type: 'tags'; tags: Tag[] }
  | { type: 'control'; session: ControlSession }
  | { type: 'pdr'; id: number; ts: number; title: string }

function loadMute(): boolean {
  try {
    return localStorage.getItem(MUTE_KEY) === '1'
  } catch {
    return false
  }
}

/** 运行操作：告警、挂牌、遥控会话。初始数据走 REST，之后由推送通道增量更新 */
export const useOps = defineStore('ops', () => {
  const unacked = shallowRef<Alarm[]>([])
  const recent = shallowRef<Alarm[]>([])
  const tags = shallowRef<Tag[]>([])
  const sessions = reactive<Record<string, ControlSession>>({})
  const muted = ref(loadMute())
  const loaded = ref(false)
  /** 最新生成的事故追忆（推送通知用） */
  const latestPdr = ref<{ id: number; ts: number; title: string } | null>(null)
  let started = false

  async function reload() {
    try {
      const [a, t] = await Promise.all([api.alarmsActive(), api.tags()])
      unacked.value = a.unacked
      recent.value = a.recent
      tags.value = t
      loaded.value = true
    } catch {
      /* 后端未就绪：下次连上推送通道时再加载 */
    }
  }

  function onBus(msg: BusMsg) {
    switch (msg.type) {
      case 'alarm': {
        const a = msg.alarm
        if (a.ack_ts === null) {
          unacked.value = [a, ...unacked.value].slice(0, MAX_UNACKED)
          if (a.level <= 2 && !muted.value) alarmSound(a.level)
        } else recent.value = [a, ...recent.value].slice(0, MAX_RECENT)
        break
      }
      case 'alarm_ack': {
        const ids = new Set(msg.ids)
        const moved = unacked.value.filter((a) => ids.has(a.id)).map((a) => ({ ...a, ack_by: msg.by, ack_ts: msg.ts }))
        if (!moved.length) break
        unacked.value = unacked.value.filter((a) => !ids.has(a.id))
        recent.value = [...moved, ...recent.value].sort((x, y) => y.id - x.id).slice(0, MAX_RECENT)
        break
      }
      case 'tags':
        tags.value = msg.tags
        break
      case 'control':
        sessions[msg.session.id] = msg.session
        break
      case 'pdr':
        latestPdr.value = { id: msg.id, ts: msg.ts, title: msg.title }
        break
    }
  }

  function start() {
    if (started) return
    started = true
    const rt = useRealtime()
    rt.onBus((m) => onBus(m as BusMsg))
    rt.onOpen(reload) // 每次（重新）连上都刷新，补上断线期间的变化
  }

  function setMuted(v: boolean) {
    muted.value = v
    try {
      localStorage.setItem(MUTE_KEY, v ? '1' : '0')
    } catch {
      /* 忽略 */
    }
  }

  const ack = (ids: number[]) => api.ack(ids)
  const ackAll = () => api.ack([], true)

  /** 各级未确认数量 */
  const counts = computed(() => {
    const c: Record<number, number> = { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 }
    for (const a of unacked.value) c[a.level]++
    return c
  })
  /** 有未确认告警的设备（接线图闪烁，直到确认） */
  const flashDevices = computed(() => new Set(unacked.value.filter((a) => a.level <= 4 && a.device).map((a) => a.device!)))
  const unackedPoints = computed(() => new Set(unacked.value.filter((a) => a.point).map((a) => a.point!)))
  const tagsByDevice = computed(() => {
    const m = new Map<string, Tag[]>()
    for (const t of tags.value) {
      if (!m.has(t.device)) m.set(t.device, [])
      m.get(t.device)!.push(t)
    }
    return m
  })
  const groundNodes = computed(() => tags.value.filter((t) => t.kind === 'ground' && t.node).map((t) => t.node!))

  function putSession(s: ControlSession) {
    sessions[s.id] = s
    return s
  }

  return {
    unacked, recent, tags, sessions, muted, loaded, latestPdr, counts, flashDevices, unackedPoints, tagsByDevice, groundNodes,
    start, reload, setMuted, ack, ackAll, putSession,
  } // prettier-ignore
})
