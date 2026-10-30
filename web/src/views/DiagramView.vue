<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { NButton, NEmpty, NSpin } from 'naive-ui'
import type { Point } from '../api/types'
import DeviceDrawer from '../components/diagram/DeviceDrawer.vue'
import SingleLine from '../components/diagram/SingleLine.vue'
import ScenarioPanel from '../components/sim/ScenarioPanel.vue'
import TrendDrawer from '../components/TrendDrawer.vue'
import { useTopology } from '../composables/useTopology'
import { useModel } from '../stores/model'
import { useOps } from '../stores/ops'
import { useRealtime } from '../stores/realtime'

const model = useModel()
const rt = useRealtime()
const ops = useOps()

const selected = ref<string | null>(null)
const trendPoint = ref<Point | null>(null)
const showSim = ref(false)
/** 两位小数，避免 -0.00 */
const fix2 = (v: number) => (Math.abs(v) < 0.005 ? 0 : v).toFixed(2)

onMounted(() => model.load())

// 临时接地线作为额外接地点参与拓扑
const { topo, kpi } = useTopology(
  () => rt.values,
  () => ops.groundNodes,
)

const channelDown = computed(() => {
  const chs = Object.values(rt.channels)
  return chs.length > 0 && chs.some((c) => c.state !== 'up')
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
            <span class="k">下网功率</span><span class="v mono">{{ fix2(kpi.import) }}</span><span class="u">MW</span>
          </div>
          <div class="kpi">
            <span class="k">10kV 负荷</span><span class="v mono">{{ fix2(kpi.load) }}</span><span class="u">MW</span>
          </div>
          <div class="kpi">
            <span class="k">光伏出力</span><span class="v mono pv">{{ fix2(kpi.pv) }}</span><span class="u">MW</span>
          </div>
          <div class="kpi" :title="kpi.dead.map((b) => b.name).join('、')">
            <span class="k">失电出线</span>
            <span class="v mono" :class="{ bad: kpi.dead.length }">{{ kpi.dead.length }}</span><span class="u">条</span>
          </div>
        </div>
        <div class="tools">
          <div class="legend">
            <span><i class="ln" style="background: var(--kv110)" />110kV</span>
            <span><i class="ln" style="background: var(--kv10)" />10kV</span>
            <span><i class="ln" style="background: var(--deenergized)" />失电</span>
            <span><i class="ln" style="background: var(--grounded)" />接地</span>
            <span><i class="box" style="background: var(--closed)" />合</span>
            <span><i class="box hollow" style="border-color: var(--open)" />分</span>
          </div>
          <n-button size="small" :type="showSim ? 'warning' : 'default'" @click="showSim = !showSim">故障注入</n-button>
        </div>
      </div>

      <div v-if="channelDown" class="banner">通道中断：画面数据不刷新（品质 NT），恢复后自动总召</div>

      <SingleLine
        :values="rt.values"
        :grounds="ops.groundNodes"
        :flash="ops.flashDevices"
        :tags="ops.tagsByDevice"
        :selected="selected"
        @select="(eq) => (selected = eq)"
        @trend="openTrend"
      />
    </template>

    <DeviceDrawer :eq-id="selected" :topo="topo" @close="selected = null" @trend="(p) => (trendPoint = p)" />
    <TrendDrawer :point="trendPoint" @close="trendPoint = null" />
    <ScenarioPanel v-model:show="showSim" />
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
.tools {
  display: flex;
  align-items: center;
  gap: 14px;
}
</style>
