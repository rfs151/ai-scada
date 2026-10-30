<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { NEmpty, NSpin } from 'naive-ui'
import type { Point } from '../api/types'
import TrendDrawer from '../components/TrendDrawer.vue'
import { fmtTime, formatValue, LIMIT_TEXT, limitState, qualityFlags } from '../lib/format'
import { useModel } from '../stores/model'
import { useRealtime } from '../stores/realtime'

const model = useModel()
const rt = useRealtime()
onMounted(() => model.load())

const selectedBay = ref('ALL')
const trendPoint = ref<Point | null>(null)

const groups = computed(() => {
  const st = model.station
  if (!st) return []
  const hv = st.bays.filter((b) => b.vl === 'HV' && b.kind !== 'transformer')
  const tr = st.bays.filter((b) => b.kind === 'transformer')
  const lv = st.bays.filter((b) => b.vl === 'LV')
  return [
    { title: '全站', bays: [{ id: 'ALL', name: '全部测点' }, { id: 'STN', name: '全站信号' }] },
    { title: '110kV', bays: hv },
    { title: '主变', bays: tr },
    { title: '10kV', bays: lv },
  ]
})

const bayName = computed(
  () => groups.value.flatMap((g) => g.bays).find((b) => b.id === selectedBay.value)?.name ?? '',
)

const bayPoints = computed(() =>
  selectedBay.value === 'ALL' ? model.points : (model.byBay.get(selectedBay.value) ?? []),
)
const analogs = computed(() => bayPoints.value.filter((p) => p.cat === 'YC' || p.cat === 'DD'))
const digitals = computed(() => bayPoints.value.filter((p) => p.cat === 'YX'))

function yxClass(p: Point) {
  const v = rt.values[p.id]?.v
  if (v === undefined) return ''
  if (!p.double) return v ? 'sig-on' : ''
  return ['pos-mid', 'pos-open', 'pos-closed', 'pos-bad'][v]
}

function limitOf(p: Point) {
  return p.cat === 'YC' ? limitState(p.limits, rt.values[p.id]?.v) : null
}

function bayStatus(bayId: string) {
  // 侧栏告警标识：间隔内有越限或品质异常
  const pts = model.byBay.get(bayId) ?? []
  let warn = false
  for (const p of pts) {
    const v = rt.values[p.id]
    if (!v) continue
    if (v.q & 0xc0) return 'bad'
    if (p.cat === 'YC' && limitState(p.limits, v.v)) warn = true
  }
  return warn ? 'warn' : ''
}
</script>

<template>
  <div class="layout">
    <aside class="bays">
      <template v-for="g in groups" :key="g.title">
        <div class="group">{{ g.title }}</div>
        <button
          v-for="b in g.bays"
          :key="b.id"
          class="bay"
          :class="[{ active: selectedBay === b.id }, bayStatus(b.id)]"
          @click="selectedBay = b.id"
        >
          {{ b.name }}
        </button>
      </template>
    </aside>

    <section class="main">
      <n-spin v-if="!model.station && !model.error" />
      <n-empty v-else-if="model.error" :description="`模型加载失败：${model.error}`" />
      <template v-else>
        <div class="head">
          <h2>{{ bayName }}</h2>
          <span class="dim small">遥测 {{ analogs.length }} · 遥信 {{ digitals.length }} · 点击遥测查看趋势</span>
        </div>

        <div class="tables">
          <div class="panel" v-if="analogs.length">
            <div class="panel-title">遥测 / 电度</div>
            <table>
              <thead>
                <tr><th>名称</th><th class="r">数值</th><th>单位</th><th>状态</th><th>时标</th></tr>
              </thead>
              <tbody>
                <tr v-for="p in analogs" :key="p.id" class="clickable" @click="trendPoint = p" :title="`${p.id} · IOA ${p.ioa}`">
                  <td>{{ p.name }}</td>
                  <td class="r mono">
                    <span :key="rt.changedAt[p.id]" class="val" :class="[limitOf(p), { flash: rt.changedAt[p.id] }]">
                      {{ formatValue(p, rt.values[p.id]) }}
                    </span>
                  </td>
                  <td class="dim">{{ p.unit }}</td>
                  <td>
                    <span v-if="limitOf(p)" class="tag" :class="limitOf(p)!">{{ LIMIT_TEXT[limitOf(p)!] }}</span>
                    <span v-for="f in qualityFlags(rt.values[p.id]?.q ?? 0)" :key="f.cls" class="tag" :class="f.cls">{{ f.label }}</span>
                  </td>
                  <td class="mono dim small">{{ rt.values[p.id] ? fmtTime(rt.values[p.id].t) : '' }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="panel" v-if="digitals.length">
            <div class="panel-title">遥信 <span class="dim small">红合绿分</span></div>
            <table>
              <thead>
                <tr><th>名称</th><th>状态</th><th>品质</th><th>时标</th></tr>
              </thead>
              <tbody>
                <tr v-for="p in digitals" :key="p.id" :title="`${p.id} · IOA ${p.ioa}`">
                  <td>{{ p.name }}</td>
                  <td>
                    <span :key="rt.changedAt[p.id]" class="yx" :class="[yxClass(p), { flash: rt.changedAt[p.id] }]">
                      {{ formatValue(p, rt.values[p.id]) }}
                    </span>
                  </td>
                  <td>
                    <span v-for="f in qualityFlags(rt.values[p.id]?.q ?? 0)" :key="f.cls" class="tag" :class="f.cls">{{ f.label }}</span>
                  </td>
                  <td class="mono dim small">{{ rt.values[p.id] ? fmtTime(rt.values[p.id].t) : '' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </template>
    </section>

    <TrendDrawer :point="trendPoint" @close="trendPoint = null" />
  </div>
</template>

<style scoped>
.layout {
  display: flex;
  gap: 16px;
  height: 100%;
  min-height: 0;
}
.bays {
  width: 180px;
  flex-shrink: 0;
  overflow-y: auto;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px;
}
.group {
  font-size: 12px;
  color: var(--text-dim);
  padding: 10px 8px 4px;
}
.bay {
  display: block;
  width: 100%;
  text-align: left;
  background: none;
  border: none;
  border-left: 3px solid transparent;
  color: var(--text);
  padding: 6px 8px;
  font: inherit;
  font-size: 13px;
  cursor: pointer;
  border-radius: 3px;
}
.bay:hover {
  background: #16213a;
}
.bay.active {
  background: #1b2742;
}
.bay.warn {
  border-left-color: var(--warn);
}
.bay.bad {
  border-left-color: var(--alarm);
}
.main {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
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
.tables {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(440px, 1fr));
  gap: 16px;
  align-items: start;
}
.panel {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: hidden;
}
.panel-title {
  padding: 8px 12px;
  font-weight: 600;
  border-bottom: 1px solid var(--border);
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
  padding: 6px 12px;
  border-bottom: 1px solid var(--border);
}
td {
  padding: 5px 12px;
  border-bottom: 1px solid #172238;
  white-space: nowrap;
}
tr.clickable {
  cursor: pointer;
}
tr.clickable:hover td {
  background: #16213a;
}
.r {
  text-align: right;
}
.dim {
  color: var(--text-dim);
}
.small {
  font-size: 12px;
}
.val {
  display: inline-block;
  padding: 0 4px;
  border-radius: 3px;
}
.val.hi,
.val.lo {
  color: var(--warn);
}
.val.hihi,
.val.lolo {
  color: var(--alarm);
  font-weight: 600;
}
.flash {
  animation: flash 0.8s ease-out;
}
@keyframes flash {
  from {
    background: rgba(59, 130, 246, 0.45);
  }
}
.yx {
  display: inline-block;
  min-width: 48px;
  text-align: center;
  padding: 1px 6px;
  border-radius: 3px;
  border: 1px solid var(--border);
}
/* 国内调度习惯：红合绿分 */
.pos-closed {
  color: #fff;
  background: var(--closed);
  border-color: var(--closed);
}
.pos-open {
  color: #fff;
  background: var(--open);
  border-color: var(--open);
}
.pos-mid {
  color: #111;
  background: var(--warn);
  animation: blink 0.6s infinite;
}
.pos-bad {
  color: #fff;
  background: #7c3aed;
}
.sig-on {
  color: #fff;
  background: var(--alarm);
  border-color: var(--alarm);
}
.tag {
  display: inline-block;
  font-size: 11px;
  padding: 0 5px;
  margin-right: 4px;
  border-radius: 3px;
  border: 1px solid currentColor;
}
.tag.hi,
.tag.lo {
  color: var(--warn);
}
.tag.hihi,
.tag.lolo,
.tag.iv {
  color: var(--alarm);
}
.tag.nt {
  color: var(--deenergized);
}
.tag.sb,
.tag.bl,
.tag.ov {
  color: #a78bfa;
}
@keyframes blink {
  50% {
    opacity: 0.3;
  }
}
@media (max-width: 720px) {
  .layout {
    flex-direction: column;
  }
  .bays {
    width: 100%;
    max-height: 180px;
  }
  .tables {
    grid-template-columns: 1fr;
  }
}
</style>
