<script setup lang="ts">
/**
 * 负荷预测：最近 1 仿真日实测 + 未来 1 仿真日预测（相似日法 + 实时修正），
 * 以及上一仿真日的日前预测回测。每个预测时段（15 仿真分钟 = 15 秒）刷新一次。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NRadioButton, NRadioGroup } from 'naive-ui'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { api } from '../api/client'
import type { LoadForecast } from '../api/types'

echarts.use([LineChart, GridComponent, TooltipComponent, LegendComponent, MarkLineComponent, CanvasRenderer])

const data = ref<LoadForecast | null>(null)
const error = ref<string | null>(null)
const mode = ref<'ahead' | 'backtest'>('ahead')
const el = ref<HTMLDivElement>()
let chart: echarts.ECharts | undefined
let ro: ResizeObserver | undefined
let timer: number | undefined
const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim()

async function load() {
  try {
    data.value = await api.loadForecast()
    error.value = null
  } catch (e) {
    error.value = (e as Error).message
  }
}

const metrics = computed(() => data.value?.backtest.metrics ?? null)
const peak = computed(() => {
  const pts = (data.value?.forecast ?? []).filter((p) => p.v !== null) as { t: number; v: number }[]
  return pts.length ? pts.reduce((a, b) => (b.v > a.v ? b : a)) : null
})
const hm = (t: number) => new Date(t).toLocaleTimeString('zh-CN', { hour12: false })

function render() {
  const d = data.value
  if (!chart || !d) return
  const line = (name: string, pts: [number, number | null][], color: string, dashed = false) => ({
    name,
    type: 'line' as const,
    showSymbol: false,
    data: pts,
    lineStyle: { width: 1.8, color, type: dashed ? ('dashed' as const) : ('solid' as const) },
    itemStyle: { color },
  })
  const series =
    mode.value === 'ahead'
      ? [
          {
            ...line('实测', d.actual.map((p) => [p.t, p.v]), '#60a5fa'),
            markLine: {
              symbol: 'none',
              silent: true,
              label: { formatter: '现在', color: css('--text-dim') },
              lineStyle: { color: css('--text-dim'), type: 'dotted' as const },
              data: [{ xAxis: d.now }],
            },
          },
          line('预测', d.forecast.map((p) => [p.t, p.v]), '#f97316', true),
        ]
      : [
          line('实测', d.backtest.series.map((p) => [p.t, p.actual]), '#60a5fa'),
          line('日前预测', d.backtest.series.map((p) => [p.t, p.forecast]), '#f97316', true),
        ]
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
        scale: true,
        nameTextStyle: { color: css('--text-dim') },
        axisLabel: { color: css('--text-dim') },
        splitLine: { lineStyle: { color: css('--border') } },
      },
      series,
    },
    { notMerge: true },
  )
}

watch([data, mode], render)
onMounted(() => {
  chart = echarts.init(el.value!, undefined, { renderer: 'canvas' })
  ro = new ResizeObserver(() => chart?.resize())
  ro.observe(el.value!)
  load()
  timer = window.setInterval(load, 15_000)
})
onBeforeUnmount(() => {
  window.clearInterval(timer)
  ro?.disconnect()
  chart?.dispose()
})
</script>

<template>
  <section class="fc">
    <div class="head">
      <div class="pt">
        10kV 负荷预测
        <span class="dim small">相似日法：前 7 个仿真日同一时刻加权平均，剔除事故停电等异常值，再按最近 1 小时实测滚动修正</span>
      </div>
      <n-radio-group v-model:value="mode" size="small">
        <n-radio-button value="ahead">未来 1 仿真日</n-radio-button>
        <n-radio-button value="backtest">上一日回测</n-radio-button>
      </n-radio-group>
    </div>
    <div v-if="error" class="dim">预测失败：{{ error }}</div>
    <div v-else-if="data" class="kpis">
      <div class="kpi">
        <span class="k">预测最大负荷</span>
        <span class="v mono">{{ peak ? peak.v.toFixed(2) : '—' }}</span><span class="u">MW</span>
        <span v-if="peak" class="dim small">{{ hm(peak.t) }}</span>
      </div>
      <div class="kpi" :title="'调度考核口径：1 − √(mean(((实际 − 预测) / 实际)²))'">
        <span class="k">上一日预测准确率</span>
        <span class="v mono" :class="{ bad: metrics && metrics.accuracy < 95 }">{{ metrics ? metrics.accuracy.toFixed(2) : '—' }}</span>
        <span class="u">%</span>
      </div>
      <div class="kpi">
        <span class="k">平均相对误差</span>
        <span class="v mono">{{ metrics ? metrics.mape.toFixed(2) : '—' }}</span><span class="u">%</span>
      </div>
      <div class="kpi">
        <span class="k">可用历史</span>
        <span class="v mono">{{ data.history_days }}</span><span class="u">仿真日</span>
      </div>
    </div>
    <div ref="el" class="chart" />
  </section>
</template>

<style scoped>
.fc {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 10px 14px;
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.pt {
  font-weight: 600;
}
.dim {
  color: var(--text-dim);
}
.small {
  font-size: 12px;
  font-weight: normal;
}
.kpis {
  display: flex;
  gap: 20px;
  flex-wrap: wrap;
  margin: 10px 0 4px;
}
.kpi {
  display: flex;
  align-items: baseline;
  gap: 6px;
}
.k {
  font-size: 12px;
  color: var(--text-dim);
}
.v {
  font-size: 18px;
  font-weight: 600;
}
.v.bad {
  color: var(--warn, #f59e0b);
}
.u {
  font-size: 12px;
  color: var(--text-dim);
}
.chart {
  height: 260px;
}
</style>
