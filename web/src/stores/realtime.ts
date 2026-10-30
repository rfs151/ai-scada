import { defineStore } from 'pinia'
import { ref, shallowReactive, shallowRef } from 'vue'
import type { Channel, RtValue, YxEvent } from '../api/types'
import { reconnectDelay } from '../lib/backoff'

export type LinkState = 'connecting' | 'open' | 'closed'

type ServerMsg =
  | { type: 'hello'; version: string }
  | { type: 'heartbeat'; seq: number; server_time: string }
  | { type: 'pong'; client_ts: number }
  | { type: 'snapshot' | 'rt'; values: Record<string, RtValue> }
  | { type: 'yx'; events: YxEvent[] }
  | { type: 'channel'; channels: Record<string, Channel> }
  | { type: 'error'; detail: string }
  | { type: 'alarm' | 'alarm_ack' | 'tags' | 'control' | 'pdr'; [k: string]: unknown }

const PING_INTERVAL_MS = 5_000
/** 超过该时间没收到任何消息，判定链路假死并主动重连（类似 104 规约的 t3 测试帧机制） */
const STALE_MS = 5_000
const MAX_EVENTS = 500

/** 全站唯一的实时 WebSocket 连接 */
export const useRealtime = defineStore('realtime', () => {
  const state = ref<LinkState>('closed')
  const serverVersion = ref('')
  const rttMs = ref<number | null>(null)
  const clockOffsetMs = ref(0) // 服务器时钟 - 本地时钟
  const lastMsgAt = ref(0)
  const reconnects = ref(0)

  /** 实时值表：点号 → 值。shallowReactive：整条替换即可触发更新，避免深层代理开销 */
  const values = shallowReactive<Record<string, RtValue>>({})
  /** 最近一次变化的本地时刻，用于界面闪烁提示 */
  const changedAt = shallowReactive<Record<string, number>>({})
  const channels = shallowRef<Record<string, Channel>>({})
  const yxEvents = shallowRef<YxEvent[]>([])
  const rxUpdates = ref(0)

  let ws: WebSocket | null = null
  let attempt = 0
  let pingTimer: number | undefined
  let reconnectTimer: number | undefined
  let started = false
  const busListeners: ((msg: { type: string }) => void)[] = []
  const openListeners: (() => void)[] = []
  /** 业务事件（告警、挂牌、遥控进度）监听 */
  const onBus = (fn: (msg: { type: string }) => void) => busListeners.push(fn)
  /** 推送通道（重新）建立时回调，用于刷新 REST 数据 */
  const onOpen = (fn: () => void) => openListeners.push(fn)

  const serverNow = () => new Date(Date.now() + clockOffsetMs.value)

  function url() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    return `${proto}://${location.host}/ws/realtime`
  }

  function send(msg: object) {
    if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg))
  }

  function applyValues(vals: Record<string, RtValue>, flash: boolean) {
    const now = Date.now()
    for (const [pid, v] of Object.entries(vals)) {
      values[pid] = v
      if (flash) changedAt[pid] = now
    }
    rxUpdates.value += Object.keys(vals).length
  }

  function connect() {
    state.value = 'connecting'
    ws = new WebSocket(url())

    ws.onopen = () => {
      state.value = 'open'
      attempt = 0
      lastMsgAt.value = Date.now()
      // 演示站点规模小，全量订阅；后端支持按画面订阅（points 传点号列表）
      send({ type: 'subscribe', points: '*' })
      for (const fn of openListeners) fn()
    }

    ws.onmessage = (ev) => {
      lastMsgAt.value = Date.now()
      const msg = JSON.parse(ev.data) as ServerMsg
      switch (msg.type) {
        case 'hello':
          serverVersion.value = msg.version
          break
        case 'heartbeat':
          // 心跳时间戳含单程时延，用 RTT/2 修正
          clockOffsetMs.value = Date.parse(msg.server_time) + (rttMs.value ?? 0) / 2 - Date.now()
          break
        case 'pong':
          rttMs.value = Date.now() - msg.client_ts
          break
        case 'snapshot':
          applyValues(msg.values, false)
          break
        case 'rt':
          applyValues(msg.values, true)
          break
        case 'yx':
          yxEvents.value = [...msg.events.reverse(), ...yxEvents.value].slice(0, MAX_EVENTS)
          break
        case 'channel':
          channels.value = msg.channels
          break
        case 'alarm':
        case 'alarm_ack':
        case 'tags':
        case 'control':
        case 'pdr':
          for (const fn of busListeners) fn(msg)
          break
      }
    }

    ws.onclose = () => {
      ws = null
      state.value = 'closed'
      if (!started) return
      reconnects.value++
      reconnectTimer = window.setTimeout(connect, reconnectDelay(attempt++))
    }
  }

  function tick() {
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    if (Date.now() - lastMsgAt.value > STALE_MS) {
      ws.close() // 触发 onclose → 重连
      return
    }
    send({ type: 'ping', ts: Date.now() })
  }

  function start() {
    if (started) return
    started = true
    connect()
    pingTimer = window.setInterval(tick, PING_INTERVAL_MS)
    window.setTimeout(tick, 300) // 尽快拿到 RTT
  }

  function stop() {
    started = false
    window.clearInterval(pingTimer)
    window.clearTimeout(reconnectTimer)
    ws?.close()
  }

  return {
    state, serverVersion, rttMs, clockOffsetMs, lastMsgAt, reconnects,
    values, changedAt, channels, yxEvents, rxUpdates,
    serverNow, start, stop, onBus, onOpen,
  } // prettier-ignore
})
