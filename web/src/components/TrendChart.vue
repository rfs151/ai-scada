<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { DataZoomComponent, GridComponent, MarkLineComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { api } from '../api/client'
import type { Point } from '../api/types'
import { useRealtime } from '../stores/realtime'

echarts.use([LineChart, GridComponent, TooltipComponent, DataZoomComponent, MarkLineComponent, CanvasRenderer])

const props = withDefaults(defineProps<{ point: Point; minutes?: number }>(), { minutes: 60 })

const rt = useRealtime()
const el = ref<HTMLDivElement>()
const status = ref('加载中…')
let chart: echarts.ECharts | undefined
let data: [number, number][] = []
let ctrl: AbortController | undefined
let ro: ResizeObserver | undefined

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim()

function limitLines(p: Point) {
  const l = p.limits ?? {}
  const lines: { yAxis: number; name: string; color: string }[] = []
  if (l.hihi !== undefined) lines.push({ yAxis: l.hihi, name: '上上限', color: css('--alarm') })
  if (l.hi !== undefined) lines.push({ yAxis: l.hi, name: '上限', color: css('--warn') })
  if (l.lo !== undefined) lines.push({ yAxis: l.lo, name: '下限', color: css('--warn') })
  if (l.lolo !== undefined) lines.push({ yAxis: l.lolo, name: '下下限', color: css('--alarm') })
  return lines.map((x) => ({
    yAxis: x.yAxis,
    name: x.name,
    lineStyle: { color: x.color, type: 'dashed' as const, width: 1 },
    label: { formatter: `${x.name} ${x.yAxis}`, color: x.color, fontSize: 10, position: 'insideEndTop' as const },
  }))
}

function render() {
  if (!chart) return
  const p = props.point
  chart.setOption(
    {
      animation: false,
      grid: { left: 56, right: 24, top: 36, bottom: 56 },
      tooltip: {
        trigger: 'axis',
        valueFormatter: (v: unknown) => `${(v as number).toFixed(p.decimals)} ${p.unit}`,
      },
      xAxis: {
        type: 'time',
        axisLine: { lineStyle: { color: css('--border') } },
        axisLabel: { color: css('--text-dim') },
        splitLine: { show: false },
      },
      yAxis: {
        type: 'value',
        scale: true,
        name: p.unit,
        nameTextStyle: { color: css('--text-dim') },
        axisLabel: { color: css('--text-dim') },
        splitLine: { lineStyle: { color: css('--border') } },
      },
      dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 12, borderColor: css('--border') }],
      series: [
        {
          type: 'line',
          // 变化存储：两次变化之间值保持不变，用阶梯线如实表达
          step: 'end',
          showSymbol: false,
          data,
          lineStyle: { width: 1.5, color: css('--accent') },
          areaStyle: { color: css('--accent'), opacity: 0.08 },
          markLine: { symbol: 'none', silent: true, data: limitLines(p) },
        },
      ],
    },
    { notMerge: true },
  )
}

async function load() {
  ctrl?.abort()
  ctrl = new AbortController()
  status.value = '加载中…'
  try {
    const h = await api.history(props.point.id, new Date(Date.now() - props.minutes * 60_000), undefined, ctrl.signal)
    data = h.data.map((r) => [r[0], r[1]])
    status.value = `${h.mode === 'raw' ? '原始变化数据' : `${(h.bucket_s ?? 60) / 60} 分钟聚合`} · ${data.length} 点`
    render()
  } catch (e) {
    if ((e as Error).name !== 'AbortError') status.value = `加载失败：${(e as Error).message}`
  }
}

// 实时追加
watch(
  () => rt.values[props.point.id],
  (v) => {
    if (!v || !chart) return
    const last = data[data.length - 1]
    if (last && v.t <= last[0]) return
    data.push([v.t, v.v])
    const cutoff = Date.now() - props.minutes * 60_000
    while (data.length && data[0][0] < cutoff) data.shift()
    chart.setOption({ series: [{ data }] })
  },
)

watch(() => props.point.id, load)

onMounted(() => {
  chart = echarts.init(el.value!, undefined, { renderer: 'canvas' })
  ro = new ResizeObserver(() => chart?.resize())
  ro.observe(el.value!)
  load()
})

onBeforeUnmount(() => {
  ctrl?.abort()
  ro?.disconnect()
  chart?.dispose()
})
</script>

<template>
  <div class="trend">
    <div class="meta">{{ status }}</div>
    <div ref="el" class="canvas" />
  </div>
</template>

<style scoped>
.trend {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.meta {
  font-size: 12px;
  color: var(--text-dim);
}
.canvas {
  flex: 1;
  min-height: 260px;
}
</style>
