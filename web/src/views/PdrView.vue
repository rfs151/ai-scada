<script setup lang="ts">
/**
 * 事故追忆（PDR）回放：从事故前 30 秒的全站断面开始，按 SOE 设备时标逐条重放变位与遥测，
 * 在接线图上重现开关动作、拓扑着色和潮流变化，可慢放、单步跳到下一个事件。
 */
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NEmpty, NSelect, NSlider, NSpin } from 'naive-ui'
import { api } from '../api/client'
import type { Pdr } from '../api/types'
import SingleLine from '../components/diagram/SingleLine.vue'
import PdrChart from '../components/pdr/PdrChart.vue'
import { fmtTime } from '../lib/format'
import { eventTime, Replay } from '../lib/replay'
import { useModel } from '../stores/model'

const route = useRoute()
const router = useRouter()
const model = useModel()

const list = ref<Pdr[]>([])
const replay = shallowRef<Replay | null>(null)
const loading = ref(false)
const error = ref('')
const cursor = ref(0)
const playing = ref(false)
const speed = ref(0.5)
const SPEEDS = [0.1, 0.25, 0.5, 1, 2, 5].map((v) => ({ label: `${v}×`, value: v }))

const id = computed(() => Number(route.params.id) || null)

async function loadList() {
  try {
    list.value = await api.pdrList()
    if (!id.value && list.value.length) void router.replace(`/pdr/${list.value[0].id}`)
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function load(pid: number) {
  loading.value = true
  playing.value = false
  try {
    const r = new Replay(await api.pdr(pid))
    replay.value = r
    cursor.value = Math.max(r.start, r.trigger - 3000)
    error.value = ''
  } catch (e) {
    error.value = (e as Error).message
    replay.value = null
  } finally {
    loading.value = false
  }
}
watch(id, (v) => v && load(v), { immediate: true })
onMounted(() => {
  model.load()
  loadList()
})

// ---- 播放
let raf = 0
let last = 0
function frame(ts: number) {
  const r = replay.value
  if (!r || !playing.value) return
  cursor.value = Math.min(r.end, cursor.value + (ts - last) * speed.value)
  last = ts
  if (cursor.value >= r.end) playing.value = false
  else raf = requestAnimationFrame(frame)
}
watch(playing, (p) => {
  cancelAnimationFrame(raf)
  if (p) {
    if (replay.value && cursor.value >= replay.value.end) cursor.value = replay.value.start
    last = performance.now()
    raf = requestAnimationFrame(frame)
  }
})
onBeforeUnmount(() => cancelAnimationFrame(raf))

function seek(t: number) {
  const r = replay.value
  if (r) cursor.value = Math.min(r.end, Math.max(r.start, t))
}
const step = (dir: 1 | -1) => {
  const r = replay.value
  if (!r) return
  playing.value = false
  const t = dir > 0 ? r.nextEvent(cursor.value) : r.prevEvent(cursor.value)
  if (t !== null) cursor.value = t
}

// ---- 回放断面
const values = computed(() => replay.value?.valuesAt(cursor.value) ?? {})
const moved = computed(() => replay.value?.movedDevices(cursor.value) ?? new Set<string>())
const rel = (t: number) => {
  const s = (t - (replay.value?.trigger ?? t)) / 1000
  return `${s >= 0 ? '+' : '−'}${Math.abs(s).toFixed(3)} s`
}
const events = computed(() => replay.value?.data.events ?? [])
const currentEvent = computed(() => {
  let idx = -1
  events.value.forEach((e, i) => {
    if (eventTime(e) <= cursor.value) idx = i
  })
  return idx
})
const DPI_TXT = ['中间', '分', '合', '不定']
const evText = (e: { v: number; double?: boolean }) => (e.double ? DPI_TXT[e.v] : e.v ? '动作' : '复归')

/** 曲线默认显示触发间隔的电流和两段 10kV 母线电压 */
const chartPoints = computed(() => {
  const bay = replay.value?.data.pdr.bay
  const cur = [`${bay}.I`, `${bay}.LI`].find((p) => model.byId.has(p))
  const fallback = bay?.startsWith('B1') ? 'L1.I' : 'T1.LI'
  return [cur ?? fallback, 'BUS1.UAB', 'BUS2.UAB']
})

const options = computed(() => list.value.map((p) => ({ label: `${fmtTime(p.ts)}  ${p.title}`, value: p.id })))

function onKey(e: KeyboardEvent) {
  if ((e.target as HTMLElement).tagName === 'INPUT') return
  if (e.key === ' ') {
    e.preventDefault()
    playing.value = !playing.value
  } else if (e.key === 'ArrowRight') step(1)
  else if (e.key === 'ArrowLeft') step(-1)
}
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <div class="page">
    <div class="head">
      <span class="h">事故追忆</span>
      <n-select
        :value="id"
        :options="options"
        size="small"
        placeholder="选择追忆记录"
        style="width: 420px; max-width: 100%"
        @update:value="(v: number) => router.push(`/pdr/${v}`)"
        @focus="loadList"
      />
      <span v-if="replay" class="dim small">
        触发 {{ fmtTime(replay.trigger, true) }} · 窗口 −{{ replay.data.pdr.pre_s }}s ~ +{{ replay.data.pdr.post_s }}s ·
        {{ events.length }} 条 SOE · {{ replay.data.analog.length }} 条遥测变化
      </span>
    </div>

    <n-empty v-if="!list.length && !loading" class="center" description="暂无事故追忆：在接线图「故障注入」中注入故障后自动生成" />
    <div v-else-if="error" class="err">{{ error }}</div>
    <n-spin v-else-if="loading || !replay || !model.station" class="center" />
    <div v-else class="body">
      <div class="main">
        <div class="stage">
          <SingleLine :values="values" :interactive="false" :highlight="moved" />
          <div class="clock">
            <div class="rel mono" :class="{ after: cursor >= replay.trigger }">T {{ rel(cursor) }}</div>
            <div class="abs mono">{{ fmtTime(cursor, true) }}</div>
          </div>
        </div>

        <div class="timeline">
          <div class="controls">
            <n-button size="small" @click="step(-1)">上一事件</n-button>
            <n-button size="small" type="primary" style="width: 64px" @click="playing = !playing">
              {{ playing ? '暂停' : '播放' }}
            </n-button>
            <n-button size="small" @click="step(1)">下一事件</n-button>
            <n-select v-model:value="speed" :options="SPEEDS" size="small" style="width: 80px" />
            <n-button size="small" quaternary @click="seek(replay.trigger - 3000)">跳到触发前 3s</n-button>
            <span class="dim small keys">空格 播放/暂停 · ← → 上/下一事件</span>
          </div>
          <div class="track">
            <n-slider
              :value="cursor"
              :min="replay.start"
              :max="replay.end"
              :step="10"
              :tooltip="false"
              @update:value="(v: number) => ((playing = false), seek(v))"
            />
            <div class="ticks">
              <i
                v-for="e in events"
                :key="e.id"
                :style="{ left: `${((eventTime(e) - replay.start) / (replay.end - replay.start)) * 100}%` }"
                :class="{ trig: Math.abs(eventTime(e) - replay.trigger) < 1 }"
              />
            </div>
          </div>
          <PdrChart :replay="replay" :points="chartPoints" :cursor="cursor" @seek="(t) => ((playing = false), seek(t))" />
        </div>
      </div>

      <aside class="soe">
        <div class="soe-h">SOE 事件顺序（设备时标）</div>
        <div class="soe-note">遥测报文不带时标（M_ME_NC_1），按主站接收时间回放，会比 SOE 滞后数百毫秒</div>
        <div class="soe-list">
          <div
            v-for="(e, i) in events"
            :key="e.id"
            class="ev"
            :class="{ past: eventTime(e) <= cursor, cur: i === currentEvent }"
            @click="(playing = false), seek(eventTime(e))"
          >
            <span class="mono t">{{ rel(eventTime(e)) }}</span>
            <span class="n">{{ e.name }}</span>
            <span class="v" :class="e.double ? (e.v === 2 ? 'closed' : 'open') : e.v ? 'on' : ''">{{ evText(e) }}</span>
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 100%;
}
.head {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.h {
  font-weight: 600;
  font-size: 15px;
}
.center {
  margin: auto;
}
.err {
  color: var(--alarm);
}
.body {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 10px;
}
.main {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 0;
}
.stage {
  flex: 1;
  min-height: 460px;
  display: flex;
  position: relative;
}
.clock {
  position: absolute;
  left: 14px;
  top: 10px;
  pointer-events: none;
}
.rel {
  font-size: 26px;
  font-weight: 700;
  color: var(--text-dim);
}
.rel.after {
  color: var(--alarm);
}
.abs {
  font-size: 12px;
  color: var(--text-dim);
}
.timeline {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px 12px 4px;
}
.controls {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.keys {
  margin-left: auto;
}
.track {
  position: relative;
  margin: 8px 0 4px;
}
.ticks {
  position: relative;
  height: 8px;
}
.ticks i {
  position: absolute;
  top: 0;
  width: 2px;
  height: 8px;
  background: #60a5fa;
  transform: translateX(-1px);
}
.ticks i.trig {
  background: var(--alarm);
  height: 10px;
}
.soe {
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 6px;
}
.soe-h {
  padding: 8px 12px;
  font-weight: 600;
  border-bottom: 1px solid var(--border);
}
.soe-note {
  padding: 6px 12px;
  font-size: 11px;
  color: var(--text-dim);
  border-bottom: 1px solid var(--border);
}
.soe-list {
  flex: 1;
  overflow-y: auto;
  padding: 4px 0;
}
.ev {
  display: grid;
  grid-template-columns: 82px 1fr auto;
  gap: 8px;
  padding: 4px 12px;
  font-size: 12px;
  cursor: pointer;
  color: var(--text-dim);
  opacity: 0.55;
}
.ev.past {
  opacity: 1;
  color: var(--text);
}
.ev.cur {
  background: #1b2742;
}
.ev:hover {
  background: #16213a;
}
.t {
  color: var(--text-dim);
}
.v.closed,
.v.on {
  color: var(--alarm);
}
.v.open {
  color: var(--ok);
}
.dim {
  color: var(--text-dim);
}
.small {
  font-size: 12px;
}
@media (max-width: 1000px) {
  .body {
    grid-template-columns: 1fr;
  }
  .soe {
    max-height: 300px;
  }
}
</style>
