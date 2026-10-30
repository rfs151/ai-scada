<script setup lang="ts">
/**
 * 一次设备图元（局部坐标以图元中心为原点，竖直放置；水平放置时整体旋转 90°）
 *  - 断路器：矩形，合位红色实心、分位绿色空心；10kV 中置柜手车断路器两端加插头符号
 *  - 隔离刀闸：静触头横线 + 动触头刀片，分位刀片张开约 30°
 *  - 接地刀：从挂接节点水平引出，下接接地符号
 *  - 主变：双绕组两圆相交，斜箭头表示有载调压（OLTC）
 * 中间态（00）/ 不定态（11）黄色虚线并闪烁，位置未知为灰色。
 */
import { computed } from 'vue'
import type { LabelPos } from '../../diagram/layout'

const props = defineProps<{
  kind: string
  x: number
  y: number
  h?: boolean
  side?: 1 | -1
  label?: LabelPos
  text: string
  /** 双点遥信值 */
  dpi?: number
  cart?: boolean
  /** 品质异常（非刷新 / 无效） */
  stale?: boolean
  flash?: boolean
  selected?: boolean
  /** 接地刀引线 / 主变高、低压绕组颜色 */
  color?: string
  color2?: string
  title?: string
}>()
defineEmits<{ select: [] }>()

const state = computed(() => {
  switch (props.dpi) {
    case 2:
      return 'closed'
    case 1:
      return 'open'
    case 0:
    case 3:
      return 'mid'
    default:
      return 'unknown'
  }
})

const swColor = computed(
  () => ({ closed: 'var(--closed)', open: 'var(--open)', mid: 'var(--warn)', unknown: 'var(--deenergized)' })[state.value],
)

const blade = computed(() => ({ closed: 'M0 12L0 -12', open: 'M0 12L12 -8', mid: 'M0 12L7 -10', unknown: 'M0 12L7 -10' })[state.value])

const transform = computed(() => `translate(${props.x} ${props.y})${props.h ? ' rotate(-90)' : ''}`)

const s = computed(() => props.side ?? 1)

const labelAttrs = computed(() => {
  const k = props.kind
  if (k === 'earth_switch') {
    const gx = props.x + s.value * 30
    return { x: gx + s.value * 12, y: props.y + 30, anchor: s.value > 0 ? 'start' : 'end' }
  }
  const off = k === 'transformer' ? 30 : props.cart ? 16 : 14
  switch (props.label ?? 'r') {
    case 'l':
      return { x: props.x - off, y: props.y + 4, anchor: 'end' }
    case 't':
      return { x: props.x, y: props.y - 18, anchor: 'middle' }
    case 'b':
      return { x: props.x, y: props.y + 30, anchor: 'middle' }
    default:
      return { x: props.x + off, y: props.y + 4, anchor: 'start' }
  }
})
</script>

<template>
  <g class="sym" :class="[state, { stale, flash, selected }]" @click.stop="$emit('select')">
    <title>{{ title }}</title>

    <!-- 断路器 -->
    <g v-if="kind === 'breaker'" :transform="transform">
      <rect class="hit" x="-16" :y="cart ? -28 : -20" width="32" :height="cart ? 56 : 40" />
      <template v-if="cart">
        <path :stroke="swColor" class="lead" d="M0 -17V-26M-6 -20L0 -25L6 -20M0 17V26M-6 20L0 25L6 20" />
      </template>
      <rect
        x="-9"
        y="-17"
        width="18"
        height="34"
        rx="2"
        :stroke="swColor"
        :fill="state === 'closed' ? swColor : 'var(--bg)'"
        class="body"
      />
      <text v-if="state === 'mid'" class="q" y="4" text-anchor="middle">?</text>
      <rect v-if="selected" class="sel" :x="-15" :y="cart ? -30 : -22" width="30" :height="cart ? 60 : 44" />
    </g>

    <!-- 隔离刀闸 -->
    <g v-else-if="kind === 'disconnector'" :transform="transform">
      <rect class="hit" x="-16" y="-20" width="32" height="40" />
      <path :stroke="swColor" class="lead" d="M0 -20V-12M-7 -12H7M0 12V20" />
      <circle cx="0" cy="12" r="2.2" :fill="swColor" />
      <path :stroke="swColor" class="blade" :d="blade" />
      <rect v-if="selected" class="sel" x="-16" y="-22" width="32" height="44" />
    </g>

    <!-- 接地刀：挂接点 (x, y) 水平引出 30px 后向下 -->
    <g v-else-if="kind === 'earth_switch'" :transform="`translate(${x} ${y})`">
      <path :stroke="color" class="lead" :d="`M0 0H${s * 30}V6`" />
      <g :transform="`translate(${s * 30} 26)`">
        <rect class="hit" x="-14" y="-20" width="28" height="46" />
        <circle cx="0" cy="12" r="2" :fill="swColor" />
        <path :stroke="swColor" class="lead" d="M-6 -12H6M0 12V20" />
        <path :stroke="swColor" class="blade" :d="blade" />
        <path class="ground" d="M-9 20H9M-6 25H6M-3 30H3" />
        <rect v-if="selected" class="sel" x="-14" y="-22" width="28" height="56" />
      </g>
    </g>

    <!-- 主变 -->
    <g v-else-if="kind === 'transformer'" :transform="transform">
      <rect class="hit" x="-26" y="-38" width="52" height="76" />
      <circle cx="0" cy="-16" r="20" :stroke="color" class="winding" />
      <circle cx="0" cy="16" r="20" :stroke="color2" class="winding" />
      <path class="oltc" d="M-22 28L20 -26M20 -26l-8 2M20 -26l-1 8" />
      <rect v-if="selected" class="sel" x="-26" y="-40" width="52" height="80" />
    </g>

    <text class="eq-no" :x="labelAttrs.x" :y="labelAttrs.y" :text-anchor="labelAttrs.anchor">{{ text }}</text>
  </g>
</template>

<style scoped>
.sym {
  cursor: pointer;
}
.hit {
  fill: transparent;
  stroke: none;
}
.body {
  stroke-width: 2;
}
.lead,
.blade {
  fill: none;
  stroke-width: 2.2;
  stroke-linecap: round;
}
.winding {
  fill: none;
  stroke-width: 2.5;
}
.oltc {
  fill: none;
  stroke: var(--text-dim);
  stroke-width: 1.3;
}
.ground {
  fill: none;
  stroke: var(--text-dim);
  stroke-width: 1.6;
}
.q {
  font-size: 13px;
  font-weight: 700;
  fill: var(--warn);
}
.mid .body,
.mid .blade {
  stroke-dasharray: 3 2;
}
.mid {
  animation: blink 0.8s infinite;
}
.flash {
  animation: blink 0.5s 6;
}
.stale {
  opacity: 0.5;
}
.sel {
  fill: none;
  stroke: var(--accent);
  stroke-width: 1.2;
  stroke-dasharray: 3 3;
}
.sym:hover .eq-no {
  fill: var(--text);
}
.eq-no {
  font-size: 11px;
  fill: var(--text-dim);
  font-family: var(--mono);
}
@keyframes blink {
  50% {
    opacity: 0.25;
  }
}
</style>
