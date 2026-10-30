<script setup lang="ts">
import { ref } from 'vue'
import { NDrawer, NDrawerContent, NRadioButton, NRadioGroup } from 'naive-ui'
import type { Point } from '../api/types'
import { formatValue } from '../lib/format'
import { useRealtime } from '../stores/realtime'
import TrendChart from './TrendChart.vue'

defineProps<{ point: Point | null }>()
const emit = defineEmits<{ close: [] }>()

const rt = useRealtime()
const minutes = ref(60)
</script>

<template>
  <n-drawer :show="!!point" :width="720" placement="right" @update:show="(v: boolean) => !v && emit('close')">
    <n-drawer-content v-if="point" :title="point.name" closable>
      <div class="trend-head">
        <span class="mono big">{{ formatValue(point, rt.values[point.id]) }}</span>
        <span class="dim">{{ point.unit }}</span>
        <n-radio-group v-model:value="minutes" size="small" class="range">
          <n-radio-button :value="30">30分钟</n-radio-button>
          <n-radio-button :value="60">1小时</n-radio-button>
          <n-radio-button :value="360">6小时</n-radio-button>
          <n-radio-button :value="1440">24小时</n-radio-button>
        </n-radio-group>
      </div>
      <div class="dim small">
        点号 {{ point.id }} · IOA {{ point.ioa }} (0x{{ point.ioa.toString(16).toUpperCase() }}) · 死区
        {{ point.deadband }} {{ point.unit }}
      </div>
      <div class="trend-box">
        <TrendChart :key="`${point.id}-${minutes}`" :point="point" :minutes="minutes" />
      </div>
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped>
.trend-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 4px;
}
.big {
  font-size: 26px;
  font-weight: 600;
}
.range {
  margin-left: auto;
}
.dim {
  color: var(--text-dim);
}
.small {
  font-size: 12px;
}
.trend-box {
  height: 380px;
  margin-top: 12px;
}
</style>
