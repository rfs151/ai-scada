<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { NButton, NEmpty, NSelect } from 'naive-ui'
import { api } from '../api/client'
import type { AuditEntry } from '../api/types'
import { fmtTime } from '../lib/format'
import { useAuth } from '../stores/auth'

const auth = useAuth()
const rows = ref<AuditEntry[]>([])
const error = ref('')
const loading = ref(false)
const action = ref('')

const ACTION_CN: Record<string, string> = {
  login: '登录',
  'control.create': '遥控发起',
  'control.supervise': '监护确认',
  'control.select': '遥控选择',
  'control.execute': '遥控执行',
  'control.cancel': '遥控撤销',
  'control.expire': '遥控超时',
  'control.recheck': '五防复核',
  'control.adjust': '遥调',
  'tag.add': '挂牌',
  'tag.remove': '摘牌',
  'alarm.ack': '告警确认',
}
const FILTERS = [
  { label: '全部操作', value: '' },
  { label: '登录', value: 'login' },
  { label: '遥控/遥调', value: 'control' },
  { label: '挂牌', value: 'tag' },
  { label: '告警确认', value: 'alarm' },
]

/** 详情摘要：挑出最有用的字段 */
function summary(d: Record<string, unknown> | null): string {
  if (!d) return ''
  const parts: string[] = []
  for (const k of ['action', 'value', 'kind', 'node', 'reason', 'stage', 'detail', 'elapsed_ms']) {
    if (d[k] !== undefined && d[k] !== null && d[k] !== '') parts.push(`${k}=${d[k]}`)
  }
  const v = d.violations as string[] | undefined
  if (v?.length) parts.push(`五防：${v.join('；')}`)
  return parts.join('  ')
}

async function load() {
  if (!auth.can('audit')) return
  loading.value = true
  try {
    rows.value = await api.audit(500, action.value || undefined)
    error.value = ''
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}
onMounted(load)
watch([action, () => auth.user], load)
</script>

<template>
  <div class="page">
    <n-empty v-if="!auth.can('audit')" description="审计日志仅监护员、管理员可查看">
      <template #extra>
        <n-button size="small" @click="auth.showLogin = true">登录</n-button>
      </template>
    </n-empty>
    <section v-else class="panel">
      <div class="panel-title">
        审计日志 <span class="dim small">登录、遥控每一步、挂牌、告警确认均留痕，只增不改</span>
        <n-select v-model:value="action" :options="FILTERS" size="small" style="width: 130px; margin-left: auto" />
        <n-button size="small" :loading="loading" @click="load">刷新</n-button>
      </div>
      <div v-if="error" class="err">{{ error }}</div>
      <div class="table">
        <table>
          <thead>
            <tr><th>时间</th><th>用户</th><th>操作</th><th>对象</th><th>结果</th><th>详情</th><th>来源</th></tr>
          </thead>
          <tbody>
            <tr v-for="r in rows" :key="r.id">
              <td class="mono small">{{ fmtTime(r.ts, true) }}</td>
              <td>{{ r.username ?? '—' }} <span class="dim small">{{ r.role ?? '' }}</span></td>
              <td>{{ ACTION_CN[r.action] ?? r.action }}</td>
              <td class="mono">{{ r.target }}</td>
              <td><span :class="r.ok ? 'ok' : 'bad'">{{ r.ok ? '成功' : '失败' }}</span></td>
              <td class="detail small">{{ summary(r.detail) }}</td>
              <td class="mono dim small">{{ r.ip }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.page {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.panel {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 6px;
}
.panel-title {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  font-weight: 600;
  border-bottom: 1px solid var(--border);
}
.table {
  flex: 1;
  overflow: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
th {
  position: sticky;
  top: 0;
  background: var(--bg-panel);
  text-align: left;
  font-weight: normal;
  color: var(--text-dim);
  padding: 6px 12px;
  border-bottom: 1px solid var(--border);
}
td {
  padding: 4px 12px;
  border-bottom: 1px solid #172238;
  white-space: nowrap;
}
td.detail {
  white-space: normal;
  color: var(--text-dim);
}
.ok {
  color: var(--ok);
}
.bad {
  color: var(--alarm);
}
.err {
  color: var(--alarm);
  padding: 8px 12px;
}
.dim {
  color: var(--text-dim);
}
.small {
  font-size: 12px;
}
</style>
