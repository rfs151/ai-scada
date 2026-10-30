<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { NEmpty, NSpin } from 'naive-ui'
import type { Equipment, Point } from '../api/types'
import DeviceDrawer from '../components/diagram/DeviceDrawer.vue'
import EquipSym from '../components/diagram/EquipSym.vue'
import MeasText from '../components/diagram/MeasText.vue'
import TermSym from '../components/diagram/TermSym.vue'
import TrendDrawer from '../components/TrendDrawer.vue'
import { layout, type FlowDef } from '../diagram/layout'
import { Q } from '../lib/format'
import { analyzeTopology, DPI_CLOSED, type NodeState } from '../lib/topology'
import { useModel } from '../stores/model'
import { useOps } from '../stores/ops'
import { useRealtime } from '../stores/realtime'

const model = useModel()
const rt = useRealtime()
const ops = useOps()

const selected = ref<string | null>(null)
const trendPoint = ref<Point | null>(null)

onMounted(() => model.load())

const equipment = computed(() => {
  const m = new Map<string, Equipment>()
  for (const b of model.station?.bays ?? []) for (const e of b.equipment) m.set(e.id, e)
  return m
})

const posOf = (eq: string) => rt.values[`${eq}.POS`]

/** 拓扑分析：任一开关变位即重新计算（规模小，全量 BFS 即可） */
const topo = computed(() => {
  const st = model.station
  if (!st) return null
  // 临时接地线作为额外接地点参与拓扑
  return analyzeTopology([...equipment.value.values()], st.sources, (id) => posOf(id)?.v === DPI_CLOSED, ops.groundNodes)
})

const VL_COLOR: Record<string, string> = { HV: 'var(--kv110)', LV: 'var(--kv10)' }
const STATE_COLOR: Record<Exclude<NodeState, 'energized'>, string> = {
  dead: 'var(--deenergized)',
  grounded: 'var(--grounded)',
  conflict: 'var(--alarm)',
}

function nodeState(node: string): NodeState {
  return topo.value?.state(node) ?? 'dead'
}
function nodeColor(node: string): string {
  const s = nodeState(node)
  return s === 'energized' ? (VL_COLOR[model.station?.nodes[node] ?? ''] ?? 'var(--text)') : STATE_COLOR[s]
}
const eqColor = (eq: string) => nodeColor(equipment.value.get(eq)?.nodes[0] ?? '')

function symProps(eq: string) {
  const e = equipment.value.get(eq)
  const v = posOf(eq)
  return {
    kind: e?.type ?? 'breaker',
    text: eq.startsWith('T') ? '' : eq,
    title: e ? `${e.id} ${e.name}` : eq,
    dpi: v?.v,
    stale: !!v && (v.q & (Q.NT | Q.IV)) !== 0,
    cart: e?.type === 'breaker' && e.vl === 'LV',
    // 有未确认的变位/事故告警时闪烁，确认后停止
    flash: ops.flashDevices.has(eq),
    selected: selected.value === eq,
    color: e ? nodeColor(e.nodes[0]) : undefined,
    color2: e?.type === 'transformer' ? nodeColor(e.nodes[1]) : undefined,
  }
}

/** 潮流箭头：仅在带电且功率超过 0.05MW 时显示，动画速度随功率增大 */
function flow(f: FlowDef) {
  const v = rt.values[f.pt]
  if (!v || Math.abs(v.v) < 0.05 || nodeState(f.node) !== 'energized') return null
  const [a, b] = v.v > 0 ? [f.y1, f.y2] : [f.y2, f.y1]
  const m = (a + b) / 2
  const down = b > a
  return {
    d: `M${f.x} ${a}V${b}`,
    arrow: down ? `M${f.x - 5} ${m - 4}L${f.x + 5} ${m - 4}L${f.x} ${m + 5}Z` : `M${f.x - 5} ${m + 4}L${f.x + 5} ${m + 4}L${f.x} ${m - 5}Z`,
    dur: `${Math.max(0.35, Math.min(2.5, 3 / (1 + Math.abs(v.v) / 2))).toFixed(2)}s`,
  }
}

// ---- 全站概况
const val = (pid: string) => rt.values[pid]?.v ?? 0
const kpi = computed(() => {
  const st = model.station
  const outlets = (st?.bays ?? []).filter((b) => b.kind === 'feeder' || b.kind === 'pv')
  const dead = outlets.filter((b) => nodeState(`${b.id}_O`) !== 'energized')
  return {
    import: val('L1.P') + val('L2.P'),
    load: (st?.bays ?? []).filter((b) => b.kind === 'feeder').reduce((s, b) => s + val(`${b.id}.P`), 0),
    pv: val('F04.P'),
    dead,
  }
})

const channelDown = computed(() => {
  const chs = Object.values(rt.channels)
  return chs.length > 0 && chs.some((c) => c.state !== 'up')
})

// ---- 挂牌标识：放在设备编号的另一侧
const TAG_TEXT: Record<string, string> = { maint: '检', noclose: '禁', ground: '地' }
const tagBadges = computed(() => {
  const out: { key: string; x: number; y: number; kind: string; text: string; title: string }[] = []
  for (const [dev, tags] of ops.tagsByDevice) {
    const sym = layout.symbols.find((s) => s.eq === dev)
    let x: number, y: number, dx: number
    if (sym) {
      if (sym.h) [x, y, dx] = [sym.x - 8, sym.y + 28, 16]
      else {
        const left = (sym.label ?? 'r') !== 'l'
        const off = equipment.value.get(dev)?.type === 'transformer' ? 40 : 24
        ;[x, y, dx] = [sym.x + (left ? -off : off), sym.y - 8, left ? -16 : 16]
      }
    } else {
      const line = layout.lines.find((l) => l.eq === dev)
      const m = line?.d.match(/M(\d+) (\d+)V(\d+)/)
      if (!m) continue
      ;[x, y, dx] = [Number(m[1]) + 14, (Number(m[2]) + Number(m[3])) / 2, 16]
    }
    tags.forEach((t, i) =>
      out.push({
        key: `${dev}-${t.kind}`,
        x: x + i * dx,
        y,
        kind: t.kind,
        text: TAG_TEXT[t.kind],
        title: `${t.kind_cn}牌${t.node ? ` @${t.node}` : ''} · ${t.created_by}${t.note ? ` · ${t.note}` : ''}`,
      }),
    )
  }
  return out
})

function openTrend(pid: string) {
  const p = model.byId.get(pid)
  if (p) trendPoint.value = p
}
</script>

<template>
  <div class="page">
    <n-spin v-if="!model.station && !model.error" class="center" />
    <n-empty v-else-if="model.error" class="center" :description="`模型加载失败：${model.error}`" />
    <template v-else>
      <div class="bar">
        <div class="kpis">
          <div class="kpi">
            <span class="k">下网功率</span><span class="v mono">{{ kpi.import.toFixed(2) }}</span><span class="u">MW</span>
          </div>
          <div class="kpi">
            <span class="k">10kV 负荷</span><span class="v mono">{{ kpi.load.toFixed(2) }}</span><span class="u">MW</span>
          </div>
          <div class="kpi">
            <span class="k">光伏出力</span><span class="v mono pv">{{ kpi.pv.toFixed(2) }}</span><span class="u">MW</span>
          </div>
          <div class="kpi" :title="kpi.dead.map((b) => b.name).join('、')">
            <span class="k">失电出线</span>
            <span class="v mono" :class="{ bad: kpi.dead.length }">{{ kpi.dead.length }}</span><span class="u">条</span>
          </div>
        </div>
        <div class="legend">
          <span><i class="ln" style="background: var(--kv110)" />110kV</span>
          <span><i class="ln" style="background: var(--kv10)" />10kV</span>
          <span><i class="ln" style="background: var(--deenergized)" />失电</span>
          <span><i class="ln" style="background: var(--grounded)" />接地</span>
          <span><i class="box" style="background: var(--closed)" />合</span>
          <span><i class="box hollow" style="border-color: var(--open)" />分</span>
        </div>
      </div>

      <div v-if="channelDown" class="banner">通道中断：画面数据不刷新（品质 NT），恢复后自动总召</div>

      <div class="canvas" @click="selected = null">
        <svg :viewBox="`0 0 ${layout.width} ${layout.height}`" preserveAspectRatio="xMidYMid meet" role="img" aria-label="一次接线图">
          <!-- 连接线（按连接节点拓扑着色） -->
          <path
            v-for="(w, i) in layout.wires"
            :key="`w${i}`"
            :d="w.d"
            class="wire"
            :class="{ bus: w.bus, conflict: nodeState(w.node) === 'conflict' }"
            :stroke="nodeColor(w.node)"
          />
          <path v-for="l in layout.lines" :key="l.eq" :d="l.d" class="wire line" :stroke="eqColor(l.eq)" @click.stop="selected = l.eq">
            <title>{{ equipment.get(l.eq)?.name }}</title>
          </path>

          <!-- 潮流 -->
          <g v-for="f in layout.flows" :key="`f${f.pt}`">
            <template v-if="flow(f)">
              <path :d="flow(f)!.d" class="flow" :style="{ animationDuration: flow(f)!.dur }" />
              <path :d="flow(f)!.arrow" class="arrow" />
            </template>
          </g>

          <TermSym v-for="(t, i) in layout.terms" :key="`t${i}`" v-bind="t" :color="nodeColor(t.node)" />

          <EquipSym
            v-for="s in layout.symbols"
            :key="s.eq"
            v-bind="{ ...s, ...symProps(s.eq) }"
            @select="selected = s.eq"
          />

          <g v-for="b in tagBadges" :key="b.key" class="tagb" :class="b.kind" :transform="`translate(${b.x} ${b.y})`">
            <title>{{ b.title }}</title>
            <rect x="-8" y="-8" width="16" height="16" rx="2" />
            <text y="4" text-anchor="middle">{{ b.text }}</text>
          </g>

          <text
            v-for="(t, i) in layout.texts"
            :key="`x${i}`"
            :x="t.x"
            :y="t.y"
            :text-anchor="t.anchor ?? 'start'"
            :class="t.cls"
          >
            {{ t.text }}
          </text>

          <MeasText
            v-for="m in layout.meas"
            :key="m.pt"
            v-bind="m"
            :point="model.byId.get(m.pt)"
            :value="rt.values[m.pt]"
            @trend="openTrend(m.pt)"
          />
        </svg>
      </div>
    </template>

    <DeviceDrawer :eq-id="selected" :topo="topo" @close="selected = null" @trend="(p) => (trendPoint = p)" />
    <TrendDrawer :point="trendPoint" @close="trendPoint = null" />
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  gap: 10px;
}
.center {
  margin: auto;
}
.bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.kpis {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.kpi {
  display: flex;
  align-items: baseline;
  gap: 6px;
  padding: 6px 12px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 6px;
}
.k {
  font-size: 12px;
  color: var(--text-dim);
}
.v {
  font-size: 18px;
  font-weight: 600;
}
.v.pv {
  color: #fbbf24;
}
.v.bad {
  color: var(--alarm);
}
.u {
  font-size: 12px;
  color: var(--text-dim);
}
.legend {
  display: flex;
  gap: 14px;
  font-size: 12px;
  color: var(--text-dim);
  flex-wrap: wrap;
}
.legend span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.ln {
  width: 18px;
  height: 3px;
  border-radius: 2px;
}
.box {
  width: 8px;
  height: 14px;
  border-radius: 1px;
}
.box.hollow {
  border: 2px solid;
}
.banner {
  padding: 6px 12px;
  border-radius: 4px;
  background: rgba(239, 68, 68, 0.15);
  border: 1px solid var(--alarm);
  color: var(--alarm);
  font-size: 13px;
}
.canvas {
  flex: 1;
  min-height: 0;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: auto;
  display: flex;
}
/* 弹性子项被拉伸到容器高度，viewBox + meet 等比缩放适配；窄屏保持最小尺寸并滚动 */
svg {
  flex: 1 0 960px;
  min-height: 560px;
  user-select: none;
}
.wire {
  fill: none;
  stroke-width: 2.5;
  stroke-linecap: square;
  transition: stroke 0.3s;
}
.wire.bus {
  stroke-width: 7;
  stroke-linecap: butt;
}
.wire.line {
  cursor: pointer;
  stroke-width: 3;
}
.wire.conflict {
  animation: blink 0.5s infinite;
}
.flow {
  fill: none;
  stroke: rgba(255, 255, 255, 0.7);
  stroke-width: 2;
  stroke-dasharray: 3 13;
  animation: flow linear infinite;
  pointer-events: none;
}
.arrow {
  fill: #fff;
  pointer-events: none;
}
.tagb rect {
  stroke: #0b1220;
  stroke-width: 1;
}
.tagb text {
  font-size: 11px;
  font-weight: 700;
  fill: #111;
}
.tagb.maint rect {
  fill: var(--warn);
}
.tagb.noclose rect {
  fill: var(--alarm);
}
.tagb.noclose text {
  fill: #fff;
}
.tagb.ground rect {
  fill: var(--grounded);
}
.bay-name {
  font-size: 13px;
  font-weight: 600;
  fill: var(--text);
}
@keyframes flow {
  to {
    stroke-dashoffset: -16;
  }
}
@keyframes blink {
  50% {
    opacity: 0.25;
  }
}
</style>
