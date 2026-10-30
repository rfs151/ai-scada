<script setup lang="ts">
/**
 * 告警查询 + 光字牌。
 * 光字牌：每个间隔的保护/告警信号一块"灯"，动作点亮（事故红、异常橙），有未确认告警时闪烁。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { NButton, NCheckbox, NSelect, useMessage } from 'naive-ui'
import { api } from '../api/client'
import type { Alarm } from '../api/types'
import { fmtTime } from '../lib/format'
import { useModel } from '../stores/model'
import { useOps } from '../stores/ops'
import { useRealtime } from '../stores/realtime'

const model = useModel()
const rt = useRealtime()
const ops = useOps()
const msg = useMessage()

// ---- 光字牌
const ACCIDENT = new Set(['PROT', 'DIFF', 'GAS_H', 'RECL', 'ACC'])
const board = computed(() =>
  (model.station?.bays ?? [])
    .map((b) => ({ id: b.id, name: b.name, signals: (model.byBay.get(b.id) ?? []).filter((p) => p.cat === 'YX' && !p.double) }))
    .concat([{ id: 'STN', name: '全站', signals: (model.byBay.get('STN') ?? []).filter((p) => p.cat === 'YX') }])
    .filter((b) => b.signals.length)
    .sort((a, b) => (a.id === 'STN' ? -1 : b.id === 'STN' ? 1 : 0)),
)
const short = (name: string, bay: string) => name.replace(bay, '').replace(/^全站/, '')

// ---- 查询
const LEVELS = [
  { label: '全部级别', value: 0 },
  { label: '事故', value: 1 },
  { label: '异常', value: 2 },
  { label: '越限', value: 3 },
  { label: '变位', value: 4 },
  { label: '告知', value: 5 },
]
const level = ref(0)
const onlyUnacked = ref(false)
const rows = ref<Alarm[]>([])
const loading = ref(false)
const more = ref(true)

async function load(append = false) {
  loading.value = true
  try {
    const page = await api.alarms({
      limit: 200,
      level: level.value || undefined,
      unacked: onlyUnacked.value || undefined,
      before_id: append && rows.value.length ? rows.value[rows.value.length - 1].id : undefined,
    })
    rows.value = append ? [...rows.value, ...page] : page
    more.value = page.length === 200
  } catch (e) {
    msg.error((e as Error).message)
  } finally {
    loading.value = false
  }
}
watch([level, onlyUnacked], () => load())
onMounted(() => {
  model.load()
  load()
})
// 新告警到达时，若在第一页则自动刷新
watch(
  () => ops.unacked[0]?.id,
  () => !loading.value && rows.value.length <= 200 && load(),
)
</script>

<template>
  <div class="page">
    <section class="panel">
      <div class="panel-title">光字牌 <span class="dim small">信号动作点亮，未确认闪烁</span></div>
      <div class="board">
        <div v-for="b in board" :key="b.id" class="bay">
          <div class="bay-name">{{ b.name }}</div>
          <div class="lamps">
            <div
              v-for="p in b.signals"
              :key="p.id"
              class="lamp"
              :class="{
                on: rt.values[p.id]?.v,
                accident: ACCIDENT.has(p.meas),
                flash: ops.unackedPoints.has(p.id),
              }"
              :title="p.name"
            >
              {{ short(p.name, b.name) }}
            </div>
          </div>
        </div>
      </div>
    </section>

    <section class="panel grow">
      <div class="panel-title">
        告警查询
        <n-select v-model:value="level" :options="LEVELS" size="small" style="width: 120px" />
        <n-checkbox v-model:checked="onlyUnacked">仅未确认</n-checkbox>
        <n-button size="small" :loading="loading" @click="load()">刷新</n-button>
      </div>
      <div class="table">
        <table>
          <thead>
            <tr><th>时间</th><th>级别</th><th>类型</th><th>内容</th><th>确认</th></tr>
          </thead>
          <tbody>
            <tr v-for="a in rows" :key="a.id" :class="`l${a.level}`">
              <td class="mono small">{{ fmtTime(a.ts, true) }}</td>
              <td><span class="lv">{{ a.level_cn }}</span></td>
              <td class="dim">{{ a.kind }}</td>
              <td class="text">{{ a.text }} <span v-if="a.suppressed" class="sup">检修抑制</span></td>
              <td class="dim small">{{ a.ack_by ? `${a.ack_by} ${a.ack_ts ? fmtTime(a.ack_ts) : ''}` : '未确认' }}</td>
            </tr>
          </tbody>
        </table>
        <div class="foot">
          <n-button v-if="more && rows.length" size="small" :loading="loading" @click="load(true)">加载更多</n-button>
          <span v-else class="dim small">共 {{ rows.length }} 条</span>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
  min-height: 0;
}
.panel {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 6px;
}
.grow {
  flex: 1;
  min-height: 200px;
  display: flex;
  flex-direction: column;
}
.panel-title {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  font-weight: 600;
  border-bottom: 1px solid var(--border);
}
.board {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  padding: 12px;
}
.bay-name {
  font-size: 12px;
  color: var(--text-dim);
  margin-bottom: 4px;
}
.lamps {
  display: flex;
  gap: 4px;
}
.lamp {
  min-width: 64px;
  padding: 6px 6px;
  text-align: center;
  font-size: 12px;
  border-radius: 3px;
  border: 1px solid #2a3756;
  background: #0f172a;
  color: #4b5a78;
}
.lamp.on {
  color: #111;
  background: #f97316;
  border-color: #f97316;
  box-shadow: 0 0 8px rgba(249, 115, 22, 0.6);
}
.lamp.on.accident {
  color: #fff;
  background: #ef4444;
  border-color: #ef4444;
  box-shadow: 0 0 8px rgba(239, 68, 68, 0.7);
}
.lamp.flash {
  animation: blink 0.6s infinite;
}
@keyframes blink {
  50% {
    opacity: 0.3;
  }
}
.table {
  flex: 1;
  overflow: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
th {
  position: sticky;
  top: 0;
  background: var(--bg-panel);
  text-align: left;
  font-weight: normal;
  color: var(--text-dim);
  padding: 6px 12px;
  border-bottom: 1px solid var(--border);
}
td {
  padding: 4px 12px;
  border-bottom: 1px solid #172238;
  white-space: nowrap;
}
td.text {
  white-space: normal;
  color: var(--lc);
}
.lv {
  font-size: 11px;
  padding: 0 6px;
  border-radius: 3px;
  color: #fff;
  background: var(--lc);
}
.sup {
  font-size: 11px;
  padding: 0 4px;
  border: 1px solid var(--warn);
  color: var(--warn);
  border-radius: 3px;
}
.foot {
  padding: 10px;
  text-align: center;
}
.l1 {
  --lc: #ef4444;
}
.l2 {
  --lc: #f97316;
}
.l3 {
  --lc: #eab308;
}
.l4 {
  --lc: #60a5fa;
}
.l5 {
  --lc: #8a98b3;
}
.dim {
  color: var(--text-dim);
}
.small {
  font-size: 12px;
}
</style>
