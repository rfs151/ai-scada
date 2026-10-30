<script setup lang="ts">
/**
 * 运行报表：负荷特性、负荷曲线、电压合格率、主变与出线运行统计，可导出 Excel。
 * 仿真时钟 60 倍速：现实 24 分钟 = 仿真 1 天，默认统计最近 1 个仿真日。
 */
import { computed, onMounted, ref } from 'vue'
import { NButton, NDatePicker, NRadioButton, NRadioGroup, NSpin, useMessage } from 'naive-ui'
import { api } from '../api/client'
import type { Report } from '../api/types'
import LoadCurve from '../components/LoadCurve.vue'
import { fixed } from '../lib/format'

const msg = useMessage()
const preset = ref<'simday' | 'hour' | 'today' | 'yesterday' | 'custom'>('simday')
const custom = ref<[number, number] | null>(null)
const report = ref<Report | null>(null)
const loading = ref(false)
const exporting = ref(false)

function range(): [Date, Date] {
  const now = new Date()
  const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  switch (preset.value) {
    case 'hour':
      return [new Date(now.getTime() - 3600_000), now]
    case 'today':
      return [midnight, now]
    case 'yesterday':
      return [new Date(midnight.getTime() - 86_400_000), midnight]
    case 'custom':
      return custom.value ? [new Date(custom.value[0]), new Date(custom.value[1])] : [new Date(now.getTime() - 24 * 60_000), now]
    default:
      return [new Date(now.getTime() - 24 * 60_000), now]
  }
}

async function load() {
  loading.value = true
  try {
    report.value = await api.report(...range())
  } catch (e) {
    msg.error((e as Error).message)
  } finally {
    loading.value = false
  }
}
async function exportXlsx() {
  exporting.value = true
  try {
    await api.reportExport(...range())
  } catch (e) {
    msg.error((e as Error).message)
  } finally {
    exporting.value = false
  }
}
onMounted(load)

const n = (v: number | null | undefined, d = 2) => (v === null || v === undefined ? '—' : fixed(v, d))
const hm = (t: number | null) => (t === null ? '' : new Date(t).toLocaleTimeString('zh-CN', { hour12: false }))
const kwh = (v: number | null) => (v === null ? '—' : Math.round(v).toLocaleString('zh-CN'))
const span = computed(() => {
  const r = report.value
  if (!r) return ''
  const f = (t: number) => new Date(t).toLocaleString('zh-CN', { hour12: false })
  return `${f(r.start)} ~ ${f(r.end)} · 曲线粒度 ${r.bucket_s / 60} 分钟`
})
const worstVoltage = computed(() => {
  const vs = (report.value?.voltage ?? []).filter((v) => v.rate !== null)
  return vs.length ? Math.min(...vs.map((v) => v.rate!)) : null
})
const trips = computed(() => (report.value?.feeders ?? []).reduce((s, f) => s + f.trips, 0))
const LEVELS = ['事故', '异常', '越限', '变位', '告知']
const OPS: Record<string, string> = { 'control.execute': '遥控', 'control.adjust': '遥调', 'tag.add': '挂牌', 'sim.inject': '场景注入' }
</script>

<template>
  <div class="page">
    <div class="head">
      <span class="h">运行报表</span>
      <div class="presets">
      <n-radio-group v-model:value="preset" size="small" @update:value="(v: string) => v !== 'custom' && load()">
        <n-radio-button value="simday">最近 1 仿真日</n-radio-button>
        <n-radio-button value="hour">最近 1 小时</n-radio-button>
        <n-radio-button value="today">今天</n-radio-button>
        <n-radio-button value="yesterday">昨天</n-radio-button>
        <n-radio-button value="custom">自定义</n-radio-button>
      </n-radio-group>
      </div>
      <n-date-picker
        v-if="preset === 'custom'"
        v-model:value="custom"
        type="datetimerange"
        size="small"
        clearable
        @update:value="load"
      />
      <n-button size="small" :loading="loading" @click="load">刷新</n-button>
      <n-button size="small" type="primary" :loading="exporting" @click="exportXlsx">导出 Excel</n-button>
      <span class="dim small">{{ span }}</span>
    </div>
    <div class="dim small hint">仿真时钟 60 倍速：现实 24 分钟 = 仿真 1 天；电量取电度表读数差（按仿真时间累计）</div>

    <n-spin v-if="!report" class="center" />
    <template v-else>
      <div class="kpis">
        <div class="kpi">
          <div class="k">最大负荷</div>
          <div class="v">{{ n(report.load.max) }}<small>MW</small></div>
          <div class="s">{{ hm(report.load.max_t) }}</div>
        </div>
        <div class="kpi">
          <div class="k">最小负荷</div>
          <div class="v">{{ n(report.load.min) }}<small>MW</small></div>
          <div class="s">{{ hm(report.load.min_t) }}</div>
        </div>
        <div class="kpi">
          <div class="k">负荷率</div>
          <div class="v">{{ n(report.load.load_factor, 1) }}<small>%</small></div>
          <div class="s">峰谷差率 {{ n(report.load.peak_valley, 1) }}%</div>
        </div>
        <div class="kpi">
          <div class="k">最低电压合格率</div>
          <div class="v" :class="{ bad: worstVoltage !== null && worstVoltage < 99 }">{{ n(worstVoltage, 2) }}<small>%</small></div>
          <div class="s">考核要求 ≥ 99%</div>
        </div>
        <div class="kpi">
          <div class="k">事故跳闸</div>
          <div class="v" :class="{ bad: trips }">{{ trips }}<small>次</small></div>
          <div class="s">事故追忆 {{ report.pdr }} 次</div>
        </div>
        <div class="kpi">
          <div class="k">告警</div>
          <div class="v">{{ Object.values(report.alarms).reduce((a, b) => a + b, 0) }}<small>条</small></div>
          <div class="s">
            <span v-for="(name, i) in LEVELS" :key="i">{{ name }} {{ report.alarms[i + 1] ?? 0 }} </span>
          </div>
        </div>
      </div>

      <section class="panel">
        <div class="pt">负荷曲线</div>
        <LoadCurve :report="report" />
      </section>

      <div class="grid">
        <section class="panel">
          <div class="pt">电压合格率 <span class="dim small">1 分钟平均值统计，不含停电时间</span></div>
          <table>
            <thead>
              <tr><th>测点</th><th>合格范围</th><th>合格率</th><th>超上限</th><th>超下限</th><th>最高</th><th>最低</th></tr>
            </thead>
            <tbody>
              <tr v-for="v in report.voltage" :key="v.point">
                <td>{{ v.name.replace('线电压Uab', '') }}</td>
                <td class="mono dim">{{ v.lo }}~{{ v.hi }}</td>
                <td class="mono">
                  <div class="bar"><i :style="{ width: `${v.rate ?? 0}%` }" :class="{ bad: (v.rate ?? 100) < 99 }" /></div>
                  <span :class="{ badt: (v.rate ?? 100) < 99 }">{{ n(v.rate, 2) }}%</span>
                </td>
                <td class="mono">{{ v.over }} 分</td>
                <td class="mono">{{ v.under }} 分</td>
                <td class="mono" :title="`出现时刻 ${hm(v.max_t)}`">{{ n(v.max, 3) }}</td>
                <td class="mono" :title="`出现时刻 ${hm(v.min_t)}`">{{ n(v.min, 3) }}</td>
              </tr>
            </tbody>
          </table>
        </section>

        <section class="panel">
          <div class="pt">主变</div>
          <table>
            <thead>
              <tr><th>主变</th><th>最大负载率</th><th>平均负载率</th><th>最高油温</th><th>调档次数</th><th>电量 kWh</th></tr>
            </thead>
            <tbody>
              <tr v-for="t in report.transformers" :key="t.bay">
                <td>{{ t.name }}</td>
                <td class="mono">{{ n(t.load_max, 1) }}% <span class="dim">{{ hm(t.load_max_t) }}</span></td>
                <td class="mono">{{ n(t.load_avg, 1) }}%</td>
                <td class="mono">{{ n(t.oil_max, 1) }}℃</td>
                <td class="mono">{{ t.tap_ops }}</td>
                <td class="mono">{{ kwh(t.energy_kwh) }}</td>
              </tr>
            </tbody>
          </table>
          <div class="pt sub">操作统计</div>
          <div class="ops">
            <span v-for="(v, k) in report.operations" :key="k">
              {{ OPS[k] ?? k }} <b>{{ v.ok }}</b><span v-if="v.fail" class="badt"> / 失败 {{ v.fail }}</span>
            </span>
            <span v-if="!Object.keys(report.operations).length" class="dim">无</span>
          </div>
        </section>
      </div>

      <section class="panel">
        <div class="pt">出线</div>
        <table>
          <thead>
            <tr><th>出线</th><th>最大电流</th><th>电流上限</th><th>最大有功</th><th>平均有功</th><th>电量 kWh</th><th>事故跳闸</th></tr>
          </thead>
          <tbody>
            <tr v-for="f in report.feeders" :key="f.bay">
              <td>{{ f.name }}<span v-if="f.kind === 'pv'" class="dim small">（上网）</span></td>
              <td class="mono" :class="{ badt: f.i_max !== null && f.i_limit !== null && f.i_max > f.i_limit }">
                {{ n(f.i_max, 1) }} A <span class="dim">{{ hm(f.i_max_t) }}</span>
              </td>
              <td class="mono dim">{{ f.i_limit ?? '—' }} A</td>
              <td class="mono">{{ n(f.p_max) }} MW</td>
              <td class="mono">{{ n(f.p_avg) }} MW</td>
              <td class="mono">{{ kwh(f.energy_kwh) }}</td>
              <td class="mono" :class="{ badt: f.trips }">{{ f.trips }}</td>
            </tr>
          </tbody>
        </table>
        <div class="dim small foot">最大电流含故障期间的短路电流</div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.h {
  font-weight: 600;
  font-size: 15px;
}
/* 窄屏下时段选择横向滚动，不撑宽页面 */
.presets {
  max-width: 100%;
  overflow-x: auto;
}
.presets .n-radio-group {
  white-space: nowrap;
}
.hint {
  margin-top: -6px;
}
.center {
  margin: 80px auto;
}
.kpis {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
  gap: 10px;
}
.kpi {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 10px 14px;
}
.k {
  font-size: 12px;
  color: var(--text-dim);
}
.v {
  font-size: 24px;
  font-weight: 700;
  font-family: var(--mono);
  margin: 2px 0;
}
.v small {
  font-size: 12px;
  font-weight: normal;
  color: var(--text-dim);
  margin-left: 4px;
}
.v.bad {
  color: var(--alarm);
}
.s {
  font-size: 11px;
  color: var(--text-dim);
}
.panel {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 10px 14px;
  overflow-x: auto;
}
.pt {
  font-weight: 600;
  margin-bottom: 8px;
}
.pt.sub {
  margin-top: 14px;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(480px, 1fr));
  gap: 12px;
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
  padding: 4px 8px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
td {
  padding: 5px 8px;
  border-bottom: 1px solid #172238;
  white-space: nowrap;
}
.bar {
  display: inline-block;
  width: 60px;
  height: 6px;
  background: #1b2742;
  border-radius: 3px;
  margin-right: 6px;
  vertical-align: middle;
  overflow: hidden;
}
.bar i {
  display: block;
  height: 100%;
  background: var(--ok);
}
.bar i.bad {
  background: var(--alarm);
}
.badt {
  color: var(--alarm);
}
.ops {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  font-size: 13px;
}
.foot {
  margin-top: 6px;
}
.dim {
  color: var(--text-dim);
}
.small {
  font-size: 12px;
}
</style>
