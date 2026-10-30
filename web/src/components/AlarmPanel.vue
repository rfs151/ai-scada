<script setup lang="ts">
/**
 * 全局告警窗（底部停靠）：分级计数、未确认列表、确认、音响开关。
 * 事故/异常未确认时标题栏闪烁，确认后停止——对应调度台"告警确认"操作习惯。
 */
import { computed, ref } from 'vue'
import { NButton, useMessage } from 'naive-ui'
import type { Alarm } from '../api/types'
import { fmtTime } from '../lib/format'
import { unlockAudio } from '../lib/sound'
import { useAuth } from '../stores/auth'
import { useOps } from '../stores/ops'

const ops = useOps()
const auth = useAuth()
const msg = useMessage()

const open = ref(false)
const tab = ref<'unacked' | 'recent'>('unacked')
const LEVELS = [
  { level: 1, name: '事故' },
  { level: 2, name: '异常' },
  { level: 3, name: '越限' },
  { level: 4, name: '变位' },
]
const filter = ref<number | null>(null)

const list = computed(() => {
  const src = tab.value === 'unacked' ? ops.unacked : ops.recent
  return filter.value ? src.filter((a) => a.level === filter.value) : src
})
const latest = computed(() => ops.unacked[0] as Alarm | undefined)
const urgent = computed(() => ops.counts[1] + ops.counts[2] > 0)

async function ack(ids: number[] | 'all') {
  if (!auth.can('ack')) {
    auth.showLogin = true
    return
  }
  try {
    const r = ids === 'all' ? await ops.ackAll() : await ops.ack(ids)
    if (ids === 'all') msg.success(`已确认 ${r.acked.length} 条告警`)
  } catch (e) {
    msg.error((e as Error).message)
  }
}

function toggleMute() {
  unlockAudio()
  ops.setMuted(!ops.muted)
}
</script>

<template>
  <section class="panel" :class="{ open }">
    <header class="bar" :class="{ urgent }" @click="open = !open">
      <span class="title">告警</span>
      <span
        v-for="l in LEVELS"
        :key="l.level"
        class="cnt"
        :class="[`l${l.level}`, { zero: !ops.counts[l.level], on: filter === l.level }]"
        :title="`未确认${l.name}告警`"
        @click.stop="(filter = filter === l.level ? null : l.level), (open = true)"
      >
        {{ l.name }} {{ ops.counts[l.level] }}
      </span>
      <span class="latest" :class="latest ? `l${latest.level}` : ''">
        <template v-if="latest">
          <span class="mono">{{ fmtTime(latest.ts) }}</span> {{ latest.text }}
        </template>
        <span v-else class="dim">无未确认告警</span>
      </span>
      <span class="actions" @click.stop>
        <button class="icon" :title="ops.muted ? '音响已关闭' : '音响已开启'" @click="toggleMute">
          {{ ops.muted ? '音响关' : '音响开' }}
        </button>
        <n-button size="tiny" :disabled="!ops.unacked.length" @click="ack('all')">全部确认</n-button>
        <button class="icon" @click="open = !open">{{ open ? '▾' : '▴' }}</button>
      </span>
    </header>

    <div v-if="open" class="body">
      <div class="tabs">
        <button :class="{ on: tab === 'unacked' }" @click="tab = 'unacked'">未确认 {{ ops.unacked.length }}</button>
        <button :class="{ on: tab === 'recent' }" @click="tab = 'recent'">最近已确认</button>
        <span v-if="filter" class="dim small">· 仅显示{{ LEVELS.find((l) => l.level === filter)?.name }}</span>
        <router-link to="/alarms" class="more">告警查询 / 光字牌 →</router-link>
      </div>
      <div class="list">
        <div v-if="!list.length" class="empty dim">暂无</div>
        <div v-for="a in list" :key="a.id" class="row" :class="`l${a.level}`">
          <span class="mono time">{{ fmtTime(a.ts, true) }}</span>
          <span class="lv">{{ a.level_cn }}</span>
          <span class="text">{{ a.text }}</span>
          <span v-if="a.ack_ts" class="dim small by">{{ a.ack_by }} 确认</span>
          <n-button v-else size="tiny" quaternary @click="ack([a.id])">确认</n-button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.panel {
  flex-shrink: 0;
  background: var(--bg-panel);
  border-top: 1px solid var(--border);
}
.bar {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 34px;
  padding: 0 12px;
  cursor: pointer;
  font-size: 13px;
  white-space: nowrap;
}
.bar.urgent {
  animation: urgent 1s infinite;
}
@keyframes urgent {
  50% {
    background: rgba(239, 68, 68, 0.18);
  }
}
.title {
  font-weight: 600;
}
.cnt {
  padding: 1px 7px;
  border-radius: 10px;
  font-size: 12px;
  color: #fff;
  background: var(--lc);
}
.cnt.zero {
  background: transparent;
  color: var(--text-dim);
  border: 1px solid var(--border);
}
.cnt.on {
  outline: 2px solid var(--text);
}
.latest {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--lc, var(--text));
}
.actions {
  display: flex;
  align-items: center;
  gap: 6px;
}
.icon {
  background: none;
  border: none;
  color: var(--text-dim);
  cursor: pointer;
  font: inherit;
  font-size: 12px;
}
.body {
  height: 240px;
  display: flex;
  flex-direction: column;
  border-top: 1px solid var(--border);
}
.tabs {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  font-size: 13px;
}
.tabs button {
  background: none;
  border: 1px solid var(--border);
  color: var(--text-dim);
  border-radius: 4px;
  padding: 2px 10px;
  cursor: pointer;
  font: inherit;
}
.tabs button.on {
  color: var(--text);
  background: #1b2742;
}
.more {
  margin-left: auto;
  color: var(--accent);
  text-decoration: none;
  font-size: 12px;
}
.list {
  flex: 1;
  overflow-y: auto;
  padding: 0 12px 8px;
}
.row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 3px 0;
  border-bottom: 1px solid #172238;
  font-size: 13px;
}
.time {
  color: var(--text-dim);
  font-size: 12px;
  flex-shrink: 0;
}
.lv {
  flex-shrink: 0;
  font-size: 11px;
  padding: 0 6px;
  border-radius: 3px;
  color: #fff;
  background: var(--lc);
}
.text {
  flex: 1;
  min-width: 0;
  color: var(--lc);
}
.by {
  flex-shrink: 0;
}
.empty {
  padding: 16px;
  text-align: center;
}
.l1 {
  --lc: #ef4444;
}
.l2 {
  --lc: #f97316;
}
.l3 {
  --lc: #eab308;
}
.l4 {
  --lc: #60a5fa;
}
.l5 {
  --lc: #8a98b3;
}
.dim {
  color: var(--text-dim);
}
.small {
  font-size: 12px;
}
@media (max-width: 720px) {
  .latest {
    display: none;
  }
}
</style>
