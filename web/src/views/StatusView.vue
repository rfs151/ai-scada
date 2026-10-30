<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { NCard, NGrid, NGridItem, NTag } from 'naive-ui'
import { fetchHealth, type HealthReport } from '../api/health'
import { useRealtime } from '../stores/realtime'

const POLL_MS = 5_000

const rt = useRealtime()
const report = ref<HealthReport | null>(null)
const apiError = ref<string | null>(null)
const lastPoll = ref<Date | null>(null)
let timer: number | undefined
let ctrl: AbortController | undefined

async function poll() {
  ctrl?.abort()
  ctrl = new AbortController()
  try {
    report.value = await fetchHealth(ctrl.signal)
    apiError.value = null
  } catch (e) {
    if ((e as Error).name === 'AbortError') return
    apiError.value = (e as Error).message
    report.value = null
  } finally {
    lastPoll.value = new Date()
  }
}

onMounted(() => {
  poll()
  timer = window.setInterval(poll, POLL_MS)
})
onBeforeUnmount(() => {
  window.clearInterval(timer)
  ctrl?.abort()
})

interface Row {
  key: string
  name: string
  role: string
  ok: boolean
  detail: string[]
}

const CH_TEXT: Record<string, string> = { up: '运行', down: '中断', connecting: '连接中' }
const ch = computed(() => rt.channels.rtu1)

const rows = computed<Row[]>(() => {
  const c = report.value?.components
  const db = c?.database
  const rds = c?.redis
  return [
    {
      key: 'api',
      name: 'SCADA 服务',
      role: 'FastAPI · REST / WebSocket',
      ok: !apiError.value && !!report.value,
      detail: report.value
        ? [`版本 ${report.value.version}`, `运行 ${fmtUptime(report.value.uptime_s)}`]
        : [apiError.value ?? '—'],
    },
    {
      key: 'db',
      name: '历史库',
      role: 'TimescaleDB · 时序 / 模型 / 事件',
      ok: db?.status === 'ok',
      detail: db
        ? db.status === 'ok'
          ? [`PostgreSQL ${db.postgres}`, `TimescaleDB ${db.timescaledb ?? '未安装'}`, `${db.latency_ms} ms`]
          : [String(db.error)]
        : ['—'],
    },
    {
      key: 'redis',
      name: '实时库',
      role: 'Redis · 实时值 / 事件总线',
      ok: rds?.status === 'ok',
      detail: rds
        ? rds.status === 'ok'
          ? [`Redis ${rds.version}`, `${rds.latency_ms} ms`]
          : [String(rds.error)]
        : ['—'],
    },
    {
      key: 'ch',
      name: '104 通道 rtu1',
      role: 'IEC 104 主站 ↔ 仿真子站',
      ok: ch.value?.state === 'up',
      detail: ch.value
        ? [
            `状态 ${CH_TEXT[String(ch.value.state)] ?? ch.value.state} · 持续 ${fmtUptime(Math.round((Date.now() - Number(ch.value.since)) / 1000))}`,
            `收 I 帧 ${ch.value.rx_i ?? 0} · 发 S 帧 ${ch.value.tx_s ?? 0} · 未确认 ${ch.value.unacked ?? 0}/12`,
            `总召 ${ch.value.gi ?? 0} 次${ch.value.gi_ms ? `（${ch.value.gi_ms} ms）` : ''} · 对时 ${ch.value.clock_sync ?? 0} 次`,
          ]
        : ['等待通道状态…'],
    },
    {
      key: 'ws',
      name: '实时推送',
      role: 'WebSocket · 心跳 / 断线重连',
      ok: rt.state === 'open',
      detail: [
        rt.state === 'open' ? `往返时延 ${rt.rttMs ?? '…'} ms` : `状态 ${rt.state}`,
        `时钟偏差 ${Math.round(rt.clockOffsetMs)} ms`,
        `重连 ${rt.reconnects} 次 · 已接收 ${rt.rxUpdates} 条更新`,
      ],
    },
  ]
})

function fmtUptime(s: number) {
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  return d ? `${d}天${h}时` : h ? `${h}时${m}分` : `${m}分${s % 60}秒`
}

const pipeline = computed(() => {
  const chUp = ch.value?.state === 'up'
  const c = report.value?.components
  return [
    { name: 'rtu-sim', desc: '仿真子站 · 潮流计算', ok: chUp },
    { name: 'fep', desc: '前置采集 · IEC 104 主站', ok: chUp },
    { name: 'redis', desc: '实时库 · 事件总线', ok: c?.redis?.status === 'ok' },
    { name: 'api', desc: 'SCADA 服务 · 历史库写入', ok: !!report.value?.workers?.db_ready },
    { name: 'web', desc: '人机界面 · WebSocket', ok: rt.state === 'open' },
  ]
})
</script>

<template>
  <div class="page">
    <div class="head">
      <h2>系统状态</h2>
      <span class="mono dim" v-if="lastPoll">每 {{ POLL_MS / 1000 }}s 刷新 · {{ lastPoll.toLocaleTimeString('zh-CN', { hour12: false }) }}</span>
    </div>

    <n-grid cols="1 s:2 l:5" responsive="screen" :x-gap="12" :y-gap="12">
      <n-grid-item v-for="r in rows" :key="r.key">
        <n-card size="small" :class="['comp', r.ok ? 'ok' : 'bad']">
          <div class="comp-head">
            <span class="name">{{ r.name }}</span>
            <n-tag size="small" :type="r.ok ? 'success' : 'error'" round>{{ r.ok ? '运行' : '异常' }}</n-tag>
          </div>
          <div class="role">{{ r.role }}</div>
          <div class="detail mono">
            <div v-for="d in r.detail" :key="d">{{ d }}</div>
          </div>
        </n-card>
      </n-grid-item>
    </n-grid>

    <n-card size="small" title="数据链路" class="pipe-card">
      <div class="pipe">
        <template v-for="(p, i) in pipeline" :key="p.name">
          <div class="node" :class="{ todo: !p.ok }">
            <div class="mono">{{ p.name }}</div>
            <div class="dim small">{{ p.desc }}</div>
          </div>
          <span v-if="i < pipeline.length - 1" class="arrow">→</span>
        </template>
      </div>
    </n-card>
  </div>
</template>

<style scoped>
.page {
  max-width: 1280px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}
.dim {
  color: var(--text-dim);
}
.small {
  font-size: 12px;
}
.comp {
  border-left: 3px solid var(--deenergized);
  height: 100%;
}
.comp.ok {
  border-left-color: var(--ok);
}
.comp.bad {
  border-left-color: var(--alarm);
}
.comp-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.name {
  font-weight: 600;
}
.role {
  color: var(--text-dim);
  font-size: 12px;
  margin: 4px 0 10px;
}
.detail {
  font-size: 13px;
  line-height: 1.7;
  word-break: break-all;
}
.pipe {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.node {
  padding: 8px 12px;
  border: 1px solid var(--ok);
  border-radius: 6px;
  min-width: 120px;
}
.node.todo {
  border-style: dashed;
  border-color: var(--alarm);
}
.arrow {
  color: var(--text-dim);
}
</style>
