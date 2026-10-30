<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { NButton, NEmpty } from 'naive-ui'
import { api } from '../api/client'
import type { YxEvent } from '../api/types'
import { COT_TEXT, DPI_TEXT, fmtTime } from '../lib/format'
import { useModel } from '../stores/model'
import { useRealtime } from '../stores/realtime'

const model = useModel()
const rt = useRealtime()
const history = ref<YxEvent[]>([])
const error = ref<string | null>(null)

async function load() {
  try {
    history.value = await api.yxEvents(300)
    error.value = null
  } catch (e) {
    error.value = (e as Error).message
  }
}
onMounted(() => {
  model.load()
  load()
})

const key = (e: YxEvent) => `${e.p}|${e.dev_t ?? e.recv_t}|${e.v}`

/** 历史 + 实时合并去重，按设备时标倒序（SOE 以设备时标为准，而不是主站接收时间） */
const events = computed(() => {
  const seen = new Set<string>()
  const out: YxEvent[] = []
  for (const e of [...rt.yxEvents, ...history.value]) {
    const k = key(e)
    if (seen.has(k)) continue
    seen.add(k)
    out.push(e)
  }
  return out.sort((a, b) => (b.dev_t ?? b.recv_t) - (a.dev_t ?? a.recv_t))
})

function info(e: YxEvent) {
  const p = model.byId.get(e.p)
  return { name: e.name ?? p?.name ?? e.p, double: e.double ?? p?.double ?? false }
}

function stateText(double: boolean, v: number | null) {
  if (v === null) return '—'
  return double ? DPI_TEXT[v] : v ? '动作' : '复归'
}

function stateClass(double: boolean, v: number) {
  if (!double) return v ? 'sig-on' : 'sig-off'
  return ['pos-mid', 'pos-open', 'pos-closed', 'pos-bad'][v]
}
</script>

<template>
  <div class="page">
    <div class="head">
      <h2>SOE 事件顺序记录</h2>
      <span class="dim small">按 RTU 设备时标排序，精确到毫秒；"传输延时" = 主站接收时间 − 设备时标</span>
      <n-button size="small" class="refresh" @click="load">刷新</n-button>
    </div>
    <n-empty v-if="error" :description="`加载失败：${error}`" />
    <n-empty v-else-if="!events.length" description="暂无变位事件（遥控操作或故障时产生）" />
    <div v-else class="panel">
      <table>
        <thead>
          <tr>
            <th>设备时标</th>
            <th>名称</th>
            <th>变位</th>
            <th>类型</th>
            <th class="r">传输延时</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="e in events" :key="key(e)">
            <td class="mono">{{ e.dev_t ? fmtTime(e.dev_t, true) : fmtTime(e.recv_t, true) }}<span v-if="!e.dev_t" class="dim">*</span></td>
            <td>{{ info(e).name }}</td>
            <td>
              <span class="yx" :class="stateClass(info(e).double, e.old ?? 0)">{{ stateText(info(e).double, e.old) }}</span>
              <span class="arrow">→</span>
              <span class="yx" :class="stateClass(info(e).double, e.v)">{{ stateText(info(e).double, e.v) }}</span>
            </td>
            <td class="dim">{{ COT_TEXT[e.cot] ?? `COT ${e.cot}` }}</td>
            <td class="r mono dim">{{ e.dev_t ? `${e.recv_t - e.dev_t} ms` : '—' }}</td>
          </tr>
        </tbody>
      </table>
      <div class="foot dim small">* 无设备时标（总召唤时发现的状态不一致），显示接收时间</div>
    </div>
  </div>
</template>

<style scoped>
.page {
  max-width: 1100px;
  margin: 0 auto;
}
.head {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}
.refresh {
  margin-left: auto;
}
.dim {
  color: var(--text-dim);
}
.small {
  font-size: 12px;
}
.panel {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow-x: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
th {
  text-align: left;
  font-weight: normal;
  color: var(--text-dim);
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
}
td {
  padding: 6px 12px;
  border-bottom: 1px solid #172238;
  white-space: nowrap;
}
.r {
  text-align: right;
}
.arrow {
  color: var(--text-dim);
  margin: 0 6px;
}
.yx {
  display: inline-block;
  min-width: 44px;
  text-align: center;
  padding: 0 6px;
  border-radius: 3px;
  color: #fff;
}
.pos-closed {
  background: var(--closed);
}
.pos-open {
  background: var(--open);
}
.pos-mid {
  background: var(--warn);
  color: #111;
}
.pos-bad {
  background: #7c3aed;
}
.sig-on {
  background: var(--alarm);
}
.sig-off {
  background: #334155;
}
.foot {
  padding: 8px 12px;
}
</style>
