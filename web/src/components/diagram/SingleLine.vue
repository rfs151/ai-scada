<script setup lang="ts">
/**
 * 一次接线图画布：图形来自布局文件，着色与数值来自数据源 values。
 * 实时画面传入实时值；事故追忆传入回放时刻的断面（interactive=false 时只看不点）。
 */
import { computed } from 'vue'
import type { RtValue, Tag } from '../../api/types'
import { useTopology } from '../../composables/useTopology'
import { layout, type FlowDef } from '../../diagram/layout'
import { Q } from '../../lib/format'
import { useModel } from '../../stores/model'
import EquipSym from './EquipSym.vue'
import MeasText from './MeasText.vue'
import TermSym from './TermSym.vue'

const props = withDefaults(
  defineProps<{
    values: Record<string, RtValue>
    grounds?: string[]
    flash?: Set<string>
    tags?: Map<string, Tag[]>
    selected?: string | null
    interactive?: boolean
    /** 高亮的设备（追忆回放中当前变位的设备） */
    highlight?: Set<string>
  }>(),
  { grounds: () => [], flash: () => new Set(), tags: () => new Map(), selected: null, interactive: true, highlight: () => new Set() },
)
const emit = defineEmits<{ select: [eq: string | null]; trend: [pid: string] }>()

const model = useModel()
const { equipment, posOf, nodeState, nodeColor } = useTopology(
  () => props.values,
  () => props.grounds,
)
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
    flash: props.flash.has(eq) || props.highlight.has(eq),
    selected: props.selected === eq,
    color: e ? nodeColor(e.nodes[0]) : undefined,
    color2: e?.type === 'transformer' ? nodeColor(e.nodes[1]) : undefined,
  }
}

/** 潮流箭头：仅在带电且功率超过 0.05MW 时显示，动画速度随功率增大 */
function flow(f: FlowDef) {
  const v = props.values[f.pt]
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

// ---- 挂牌标识：放在设备编号的另一侧
const TAG_TEXT: Record<string, string> = { maint: '检', noclose: '禁', ground: '地' }
const tagBadges = computed(() => {
  const out: { key: string; x: number; y: number; kind: string; text: string; title: string }[] = []
  for (const [dev, tags] of props.tags) {
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

function select(eq: string) {
  if (props.interactive) emit('select', eq)
}
</script>

<template>
  <div class="canvas" :class="{ readonly: !interactive }" @click="interactive && emit('select', null)">
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
      <path v-for="l in layout.lines" :key="l.eq" :d="l.d" class="wire line" :stroke="eqColor(l.eq)" @click.stop="select(l.eq)">
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

      <EquipSym v-for="s in layout.symbols" :key="s.eq" v-bind="{ ...s, ...symProps(s.eq) }" @select="select(s.eq)" />

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
        :value="values[m.pt]"
        @trend="interactive && emit('trend', m.pt)"
      />
    </svg>
  </div>
</template>

<style scoped>
.canvas {
  flex: 1;
  min-height: 0;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: auto;
  display: flex;
}
.readonly :deep(.sym),
.readonly .wire.line,
.readonly :deep(.meas) {
  cursor: default;
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
