<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { NButton, NCheckbox, NInput } from 'naive-ui'
import { api } from '../api/client'
import type { Frame } from '../api/types'
import { fmtTime } from '../lib/format'

const CHANNEL = 'rtu1'
const MAX = 600
const POLL_MS = 1000

const frames = ref<Frame[]>([])
const paused = ref(false)
const showHex = ref(true)
const hideS = ref(false)
const filter = ref('')
const error = ref<string | null>(null)
let lastId: string | undefined
let timer: number | undefined
let busy = false

async function poll() {
  if (paused.value || busy) return
  busy = true
  try {
    const batch = await api.frames(CHANNEL, lastId, 300)
    if (batch.length) {
      lastId = batch[batch.length - 1].id
      frames.value = [...batch.reverse(), ...frames.value].slice(0, MAX)
    }
    error.value = null
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy = false
  }
}

onMounted(() => {
  poll()
  timer = window.setInterval(poll, POLL_MS)
})
onBeforeUnmount(() => window.clearInterval(timer))

const shown = computed(() => {
  const f = filter.value.trim().toLowerCase()
  return frames.value.filter(
    (x) => (!hideS.value || !x.text.startsWith('S ')) && (!f || x.text.toLowerCase().includes(f) || x.hex.includes(f)),
  )
})

function kind(text: string) {
  return text[0] // I / S / U
}
</script>

<template>
  <div class="page">
    <div class="head">
      <h2>报文监视 <span class="dim small">通道 {{ CHANNEL }} · IEC 60870-5-104</span></h2>
      <div class="tools">
        <n-input v-model:value="filter" size="small" placeholder="过滤：类型 / IOA / 十六进制" clearable style="width: 220px" />
        <n-checkbox v-model:checked="hideS" size="small">隐藏 S 帧</n-checkbox>
        <n-checkbox v-model:checked="showHex" size="small">显示原码</n-checkbox>
        <n-button size="small" :type="paused ? 'warning' : 'default'" @click="paused = !paused">{{ paused ? '继续' : '暂停' }}</n-button>
        <n-button size="small" @click="frames = []">清空</n-button>
      </div>
    </div>
    <div v-if="error" class="err">读取失败：{{ error }}</div>
    <div class="panel mono">
      <div v-for="f in shown" :key="f.id" class="row" :class="f.dir">
        <span class="t">{{ fmtTime(f.t) }}</span>
        <span class="dir">{{ f.dir === 'tx' ? '主站 →' : '← 子站' }}</span>
        <span class="k" :class="kind(f.text)">{{ kind(f.text) }}</span>
        <div class="body">
          <div class="text">{{ f.text.slice(1).trim() }}</div>
          <div v-if="showHex" class="hex">{{ f.hex }}</div>
        </div>
      </div>
      <div v-if="!shown.length" class="dim empty">暂无报文</div>
    </div>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  height: 100%;
  gap: 12px;
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}
.tools {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.dim {
  color: var(--text-dim);
}
.small {
  font-size: 12px;
  font-weight: normal;
}
.err {
  color: var(--alarm);
}
.panel {
  flex: 1;
  overflow: auto;
  background: #070c17;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 12px;
  padding: 4px 0;
}
.row {
  display: grid;
  grid-template-columns: 96px 56px 20px 1fr;
  gap: 8px;
  padding: 3px 12px;
  border-bottom: 1px solid #0f1729;
}
.row.tx .dir {
  color: #60a5fa;
}
.row.rx .dir {
  color: #34d399;
}
.t {
  color: var(--text-dim);
}
.k {
  font-weight: 700;
  text-align: center;
  border-radius: 3px;
}
.k.I {
  color: #fbbf24;
}
.k.S {
  color: #64748b;
}
.k.U {
  color: #c084fc;
}
.text {
  color: var(--text);
  word-break: break-all;
}
.hex {
  color: #64748b;
  word-break: break-all;
}
.empty {
  padding: 24px;
  text-align: center;
}
@media (max-width: 720px) {
  .row {
    grid-template-columns: 80px 1fr;
  }
  .dir,
  .k {
    display: none;
  }
}
</style>
