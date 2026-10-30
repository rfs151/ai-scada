<script setup lang="ts">
/**
 * 故障注入面板（仿真）：下发场景到仿真子站，并展示站内自动装置状态。
 * 面板不遮挡接线图，注入后可直接观察开关变位、拓扑着色和告警。
 */
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { NButton, NDrawer, NDrawerContent, NSelect, NTag, useMessage } from 'naive-ui'
import { api } from '../../api/client'
import type { Scenario, SimInfo, SimState } from '../../api/types'
import { useAuth } from '../../stores/auth'

const show = defineModel<boolean>('show', { required: true })
const auth = useAuth()
const msg = useMessage()

const info = ref<SimInfo | null>(null)
const state = ref<SimState | null>(null)
const target = reactive<Record<string, string | null>>({ feeder: 'F02', line: 'L1', trafo: 'T1' })
const busy = ref<string | null>(null)
let timer: number | undefined

async function poll() {
  try {
    state.value = await api.simState()
  } catch {
    state.value = null
  }
}

watch(
  show,
  async (v) => {
    window.clearInterval(timer)
    if (!v) return
    try {
      info.value = await api.simInfo()
      state.value = info.value.state
    } catch (e) {
      msg.error((e as Error).message)
    }
    timer = window.setInterval(poll, 1000)
  },
  { immediate: true },
)
onBeforeUnmount(() => window.clearInterval(timer))

const faults = computed(() => info.value?.scenarios.filter((s) => !s.restore) ?? [])
const restores = computed(() => info.value?.scenarios.filter((s) => s.restore) ?? [])
const options = (kind: string) => (info.value?.targets[kind] ?? []).map((t) => ({ label: t.name, value: t.id }))
const recloseWaiting = computed(() => Object.entries(state.value?.reclose ?? {}).filter(([, s]) => s > 0))

async function inject(s: Scenario) {
  if (!auth.can('sim')) {
    auth.showLogin = true
    return
  }
  busy.value = s.id
  try {
    const r = await api.simInject(s.id, s.target ? target[s.target] : null)
    msg.success(r.detail)
    void poll()
  } catch (e) {
    msg.error((e as Error).message)
  } finally {
    busy.value = null
  }
}

const KIND_CN = { short: '相间短路', ground: '单相接地', trafo: '主变内部故障' }
const clock = (t: number) => new Date(t * 1000).toLocaleTimeString('zh-CN', { hour12: false })
</script>

<template>
  <n-drawer v-model:show="show" :width="380" placement="right" :show-mask="false" :trap-focus="false" :block-scroll="false">
    <n-drawer-content title="故障注入（仿真）" closable :native-scrollbar="false">
      <p class="note">
        经仿真控制通道下发到仿真子站，与 104 数据通道无关。保护、重合闸、备自投由子站内装置逻辑自动完成，主站只看到
        SOE、告警和遥测变化——与真实变电站一致。
      </p>
      <div v-if="!auth.can('sim')" class="note warn">
        访客只读，<a class="link" @click="auth.showLogin = true">登录</a>后可注入场景。
      </div>

      <!-- 自动装置状态 -->
      <section class="status">
        <div class="h">自动装置</div>
        <div v-if="!state" class="dim small">仿真子站未连接</div>
        <template v-else>
          <div v-for="(b, id) in state.bzt" :key="id" class="row">
            <span>{{ b.name }}</span>
            <n-tag v-if="b.acting" size="small" type="error">动作中</n-tag>
            <n-tag v-else-if="b.charged" size="small" type="success">已充电</n-tag>
            <n-tag v-else-if="b.charging_s !== null" size="small" type="warning">充电中 {{ b.charging_s }}s</n-tag>
            <n-tag v-else size="small">未充电（运行方式不满足）</n-tag>
          </div>
          <div v-if="recloseWaiting.length" class="row">
            <span>重合闸充电</span>
            <span class="dim small">{{ recloseWaiting.map(([b, s]) => `${b} ${s}s`).join('、') }}</span>
          </div>
          <div v-if="state.faults.length || state.line_lost.length" class="row faults">
            <span>当前故障</span>
            <span>
              <n-tag v-for="f in state.faults" :key="f.bay" size="small" type="error" class="ft">
                {{ f.name }} {{ KIND_CN[f.kind] }}{{ f.kind === 'short' && !f.permanent ? '（瞬时）' : '' }}
              </n-tag>
              <n-tag v-for="l in state.line_lost" :key="l" size="small" type="error" class="ft">{{ l }} 线路失电</n-tag>
            </span>
          </div>
          <div v-if="state.outage_s" class="row"><span>通道中断</span><n-tag size="small" type="error">剩余 {{ state.outage_s }}s</n-tag></div>
          <div v-if="Math.abs(state.grid_vm - 1.03) > 0.001" class="row">
            <span>上级电压</span><n-tag size="small" type="warning">{{ state.grid_vm.toFixed(3) }} pu</n-tag>
          </div>
        </template>
      </section>

      <!-- 场景 -->
      <section v-for="s in faults" :key="s.id" class="card">
        <div class="title">{{ s.name }}</div>
        <div class="desc">{{ s.desc }}</div>
        <div class="act">
          <n-select v-if="s.target" v-model:value="target[s.target]" :options="options(s.target)" size="small" />
          <n-button size="small" type="error" ghost :loading="busy === s.id" @click="inject(s)">注入</n-button>
        </div>
      </section>
      <section class="card restore">
        <div v-for="s in restores" :key="s.id" class="restore-row">
          <div>
            <div class="title">{{ s.name }}</div>
            <div class="desc">{{ s.desc }}</div>
          </div>
          <n-button size="small" :loading="busy === s.id" @click="inject(s)">执行</n-button>
        </div>
      </section>

      <!-- 装置动作记录 -->
      <section v-if="state?.events.length" class="status">
        <div class="h">装置动作记录 <span class="dim small">（子站侧）</span></div>
        <div v-for="(e, i) in state.events" :key="i" class="ev">
          <span class="mono dim">{{ clock(e.t) }}</span> {{ e.text }}
        </div>
      </section>
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped>
.note {
  font-size: 12px;
  color: var(--text-dim);
  margin: 0 0 10px;
  line-height: 1.6;
}
.note.warn {
  color: var(--warn);
}
.link {
  color: var(--accent);
  cursor: pointer;
}
.h {
  font-weight: 600;
  margin-bottom: 6px;
}
.status {
  margin-bottom: 12px;
  padding: 10px;
  background: #0f172a;
  border-radius: 6px;
  font-size: 13px;
}
.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 3px 0;
}
.row.faults {
  align-items: flex-start;
}
.ft {
  margin: 0 0 4px 4px;
}
.card {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 10px;
  margin-bottom: 8px;
}
.title {
  font-weight: 600;
  font-size: 14px;
}
.desc {
  font-size: 12px;
  color: var(--text-dim);
  margin: 4px 0 8px;
  line-height: 1.5;
}
.act {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}
.act .n-select {
  flex: 1;
}
.restore-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.restore-row + .restore-row {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--border);
}
.restore .desc {
  margin-bottom: 0;
}
.ev {
  font-size: 12px;
  padding: 2px 0;
}
.dim {
  color: var(--text-dim);
}
.small {
  font-size: 12px;
}
</style>
