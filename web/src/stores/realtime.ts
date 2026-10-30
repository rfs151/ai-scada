import { defineStore } from 'pinia'
import { ref } from 'vue'
import { reconnectDelay } from '../lib/backoff'

export type LinkState = 'connecting' | 'open' | 'closed'

type ServerMsg =
  | { type: 'hello'; version: string }
  | { type: 'heartbeat'; seq: number; server_time: string }
  | { type: 'pong'; client_ts: number }

const PING_INTERVAL_MS = 5_000
/** 超过该时间没收到任何消息，判定链路假死并主动重连（类似 104 规约的 t3 测试帧机制） */
const STALE_MS = 5_000

/** 全站唯一的实时 WebSocket 连接 */
export const useRealtime = defineStore('realtime', () => {
  const state = ref<LinkState>('closed')
  const serverVersion = ref('')
  const rttMs = ref<number | null>(null)
  const clockOffsetMs = ref(0) // 服务器时钟 - 本地时钟
  const lastMsgAt = ref(0)
  const reconnects = ref(0)

  let ws: WebSocket | null = null
  let attempt = 0
  let pingTimer: number | undefined
  let reconnectTimer: number | undefined
  let started = false

  const serverNow = () => new Date(Date.now() + clockOffsetMs.value)

  function url() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    return `${proto}://${location.host}/ws/realtime`
  }

  function connect() {
    state.value = 'connecting'
    ws = new WebSocket(url())

    ws.onopen = () => {
      state.value = 'open'
      attempt = 0
      lastMsgAt.value = Date.now()
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
    ws.send(JSON.stringify({ type: 'ping', ts: Date.now() }))
  }

  function start() {
    if (started) return
    started = true
    connect()
    pingTimer = window.setInterval(tick, PING_INTERVAL_MS)
    // 发一次即时 ping，尽快拿到 RTT
    window.setTimeout(tick, 300)
  }

  function stop() {
    started = false
    window.clearInterval(pingTimer)
    window.clearTimeout(reconnectTimer)
    ws?.close()
  }

  return { state, serverVersion, rttMs, clockOffsetMs, lastMsgAt, reconnects, serverNow, start, stop }
})
