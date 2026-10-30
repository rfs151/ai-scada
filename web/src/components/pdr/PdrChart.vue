<script setup lang="ts">
/** 事故追忆关键量曲线：横轴为相对触发时刻的秒数，竖线为当前回放时刻，点击曲线跳转 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { Replay } from '../../lib/replay'
import { useModel } from '../../stores/model'

echarts.use([LineChart, GridComponent, TooltipComponent, LegendComponent, MarkLineComponent, CanvasRenderer])

const props = defineProps<{ replay: Replay; points: string[]; cursor: number }>()
const emit = defineEmits<{ seek: [t: number] }>()
const model = useModel()
const el = ref<HTMLDivElement>()
let chart: echarts.ECharts | undefined
let ro: ResizeObserver | undefined

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim()
const rel = (t: number) => (t - props.replay.trigger) / 1000
const COLORS = ['#f97316', '#60a5fa', '#a78bfa', '#34d399']

function series() {
  const r = props.replay
  return props.points.map((pid, i) => {
    const p = model.byId.get(pid)
    const data: [number, number][] = []
    const init = r.data.init[pid]
    if (init) data.push([rel(r.start), init[0]])
    for (const c of r.changes) if (c.pid === pid) data.push([rel(c.t), c.v])
    if (data.length) data.push([rel(r.end), data[data.length - 1][1]])
    return {
      name: p ? `${p.name}(${p.unit})` : pid,
      type: 'line' as const,
      step: 'end' as const,
      showSymbol: false,
      yAxisIndex: p?.unit === 'kV' ? 1 : 0,
      data,
      lineStyle: { width: 1.5, color: COLORS[i % COLORS.length] },
      itemStyle: { color: COLORS[i % COLORS.length] },
    }
  })
}

function cursorLine() {
  return {
    symbol: 'none',
    silent: true,
    animation: false,
    data: [
      { xAxis: 0, lineStyle: { color: css('--alarm'), type: 'dashed' as const }, label: { formatter: '触发', color: css('--alarm') } },
      { xAxis: rel(props.cursor), lineStyle: { color: '#fff', width: 1.5 }, label: { formatter: '' } },
    ],
  }
}

function render() {
  if (!chart) return
  const s = series()
  if (s.length) (s[0] as Record<string, unknown>).markLine = cursorLine()
  const axis = (name: string) => ({
    type: 'value' as const,
    scale: true,
    name,
    nameTextStyle: { color: css('--text-dim') },
    axisLabel: { color: css('--text-dim') },
    splitLine: { lineStyle: { color: css('--border') } },
  })
  chart.setOption(
    {
      animation: false,
      grid: { left: 52, right: 52, top: 30, bottom: 26 },
      legend: { top: 0, textStyle: { color: css('--text-dim'), fontSize: 11 } },
      tooltip: { trigger: 'axis', axisPointer: { type: 'line' } },
      xAxis: {
        type: 'value',
        min: rel(props.replay.start),
        max: rel(props.replay.end),
        axisLabel: { color: css('--text-dim'), formatter: (v: number) => `${v > 0 ? '+' : ''}${v}s` },
        splitLine: { show: false },
      },
      // 单位见图例，不再显示轴名（避免与"触发"标注重叠）
      yAxis: [axis(''), { ...axis(''), splitLine: { show: false } }],
      series: s,
    },
    { notMerge: true },
  )
}

watch(() => [props.replay, props.points], render)
watch(
  () => props.cursor,
  () => chart?.setOption({ series: [{ markLine: cursorLine() }] }),
)

onMounted(() => {
  chart = echarts.init(el.value!, undefined, { renderer: 'canvas' })
  chart.getZr().on('click', (e) => {
    const [x] = chart!.convertFromPixel({ gridIndex: 0 }, [e.offsetX, e.offsetY]) as number[]
    if (Number.isFinite(x)) emit('seek', props.replay.trigger + x * 1000)
  })
  ro = new ResizeObserver(() => chart?.resize())
  ro.observe(el.value!)
  render()
})
onBeforeUnmount(() => {
  ro?.disconnect()
  chart?.dispose()
})
</script>

<template>
  <div ref="el" class="chart" />
</template>

<style scoped>
.chart {
  height: 150px;
  cursor: crosshair;
}
</style>
