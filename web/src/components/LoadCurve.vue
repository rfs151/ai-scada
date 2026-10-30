<script setup lang="ts">
/** 报表负荷曲线：下网功率、10kV 负荷、光伏出力 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, MarkPointComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { Report } from '../api/types'

echarts.use([LineChart, GridComponent, TooltipComponent, LegendComponent, MarkPointComponent, CanvasRenderer])

const props = defineProps<{ report: Report }>()
const el = ref<HTMLDivElement>()
let chart: echarts.ECharts | undefined
let ro: ResizeObserver | undefined
const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim()

function render() {
  if (!chart) return
  const c = props.report.curve
  const line = (name: string, key: 'import' | 'load' | 'pv', color: string, area = false) => ({
    name,
    type: 'line' as const,
    showSymbol: false,
    data: c.map((p) => [p.t, p[key]]),
    lineStyle: { width: 1.8, color },
    itemStyle: { color },
    areaStyle: area ? { color, opacity: 0.1 } : undefined,
  })
  const load = line('10kV 负荷', 'load', '#f97316', true)
  const lmax = props.report.load
  if (lmax.max_t !== null)
    Object.assign(load, {
      markPoint: {
        symbolSize: 40,
        label: { fontSize: 10, color: '#fff' },
        data: [{ coord: [lmax.max_t, lmax.max], value: lmax.max?.toFixed(1), name: '最大' }],
      },
    })
  chart.setOption(
    {
      animation: false,
      grid: { left: 48, right: 20, top: 34, bottom: 28 },
      legend: { top: 0, textStyle: { color: css('--text-dim') } },
      tooltip: { trigger: 'axis', valueFormatter: (v: unknown) => (v === null ? '—' : `${(v as number).toFixed(2)} MW`) },
      xAxis: { type: 'time', axisLabel: { color: css('--text-dim') }, axisLine: { lineStyle: { color: css('--border') } } },
      yAxis: {
        type: 'value',
        name: 'MW',
        nameTextStyle: { color: css('--text-dim') },
        axisLabel: { color: css('--text-dim') },
        splitLine: { lineStyle: { color: css('--border') } },
      },
      series: [line('下网功率', 'import', '#60a5fa'), load, line('光伏出力', 'pv', '#fbbf24')],
    },
    { notMerge: true },
  )
}

watch(() => props.report, render)
onMounted(() => {
  chart = echarts.init(el.value!, undefined, { renderer: 'canvas' })
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
  height: 280px;
}
</style>
