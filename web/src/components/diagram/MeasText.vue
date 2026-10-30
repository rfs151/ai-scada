<script setup lang="ts">
/** 接线图量测标注：越限变色（越上下限黄、越上上/下下限红闪），品质异常灰显，点击看趋势 */
import { computed } from 'vue'
import type { Point, RtValue } from '../../api/types'
import { formatValue, limitState, Q, qualityFlags } from '../../lib/format'

const props = defineProps<{
  x: number
  y: number
  label: string
  anchor?: 'start' | 'end' | 'middle'
  point?: Point
  value?: RtValue
}>()
defineEmits<{ trend: [] }>()

const cls = computed(() => {
  const v = props.value
  if (!v || !props.point) return 'none'
  if (v.q & (Q.IV | Q.NT)) return 'stale'
  return limitState(props.point.limits, v.v) ?? ''
})

const tip = computed(() => {
  const p = props.point
  if (!p) return ''
  const flags = qualityFlags(props.value?.q ?? 0).map((f) => f.label)
  return `${p.name}${flags.length ? `（${flags.join('、')}）` : ''}\n点击查看趋势`
})
</script>

<template>
  <text class="meas" :class="cls" :x="x" :y="y" :text-anchor="anchor ?? 'start'" @click.stop="$emit('trend')">
    <title>{{ tip }}</title>
    <tspan class="lbl">{{ label }}</tspan>
    <tspan class="val" dx="5">{{ point ? formatValue(point, value) : '—' }}</tspan>
    <tspan v-if="point?.unit" class="unit" dx="3">{{ point.unit }}</tspan>
  </text>
</template>

<style scoped>
.meas {
  font-size: 12px;
  cursor: pointer;
}
.lbl,
.unit {
  fill: var(--text-dim);
}
.val {
  fill: #9fe8ff;
  font-family: var(--mono);
  font-weight: 600;
}
.meas:hover .val {
  text-decoration: underline;
}
.hi .val,
.lo .val {
  fill: var(--warn);
}
.hihi .val,
.lolo .val {
  fill: var(--alarm);
  animation: blink 0.8s infinite;
}
.stale .val {
  fill: var(--deenergized);
  font-style: italic;
}
@keyframes blink {
  50% {
    opacity: 0.3;
  }
}
</style>
