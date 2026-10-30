<script setup lang="ts">
/**
 * 遥控对话框：五防校验 + 复诵 → 监护确认 → 选择（返校）→ 执行
 * 会话状态以服务端为准（REST 返回 + 推送通道更新），前端只负责展示与发起下一步。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NButton, NInput, NModal, NStep, NSteps, useMessage } from 'naive-ui'
import { api } from '../../api/client'
import type { CheckResult, ControlSession } from '../../api/types'
import { fmtTime } from '../../lib/format'
import { useAuth } from '../../stores/auth'
import { useOps } from '../../stores/ops'

const props = defineProps<{ device: string; deviceName: string; action: 'open' | 'close' | null }>()
const emit = defineEmits<{ close: [] }>()

const ops = useOps()
const auth = useAuth()
const msg = useMessage()

const check = ref<CheckResult | null>(null)
const checking = ref(false)
const confirm = ref('')
const supUser = ref('')
const supPass = ref('')
const busy = ref(false)
const sid = ref<string | null>(null)

const session = computed<ControlSession | null>(() => (sid.value ? (ops.sessions[sid.value] ?? null) : null))
const actionCn = computed(() => (props.action === 'close' ? '合闸' : '分闸'))

// ---- 倒计时：以收到会话更新时的剩余秒数为基准本地递减
const deadline = ref(0)
const now = ref(Date.now())
let timer: number | undefined
watch(
  () => session.value?.remaining_s,
  (r) => {
    if (r !== null && r !== undefined) deadline.value = Date.now() + r * 1000
  },
)
const remaining = computed(() => Math.max(0, Math.ceil((deadline.value - now.value) / 1000)))

watch(
  () => props.action,
  async (a) => {
    sid.value = null
    confirm.value = ''
    supPass.value = ''
    check.value = null
    window.clearInterval(timer)
    if (!a) return
    timer = window.setInterval(() => (now.value = Date.now()), 250)
    if (auth.config.demo && !supUser.value) supUser.value = 'wang'
    checking.value = true
    try {
      check.value = await api.controlCheck(`${props.device}.YK`, a)
    } catch (e) {
      msg.error((e as Error).message)
    } finally {
      checking.value = false
    }
  },
  { immediate: true },
)
onBeforeUnmount(() => window.clearInterval(timer))

const stepIndex = computed(() => {
  const s = session.value?.state
  if (!s) return 1
  return { pending: 2, authorized: 3, selected: 4, done: 5, rejected: 1, failed: 4, cancelled: 4, expired: 4 }[s]
})
const stepStatus = computed(() => {
  const s = session.value?.state
  if (s === 'done') return 'finish'
  if (s && ['rejected', 'failed', 'cancelled', 'expired'].includes(s)) return 'error'
  return 'process'
})
const finished = computed(() => !!session.value && !['pending', 'authorized', 'selected'].includes(session.value.state))

async function run(fn: () => Promise<ControlSession>) {
  busy.value = true
  try {
    const s = ops.putSession(await fn())
    sid.value = s.id
  } catch (e) {
    msg.error((e as Error).message)
  } finally {
    busy.value = false
  }
}

const start = () => run(() => api.controlCreate(`${props.device}.YK`, props.action!, confirm.value))
const supervise = () =>
  run(async () => {
    const s = await api.controlSupervise(sid.value!, supUser.value, supPass.value)
    supPass.value = ''
    return s
  })
const select = () => run(() => api.controlSelect(sid.value!))
const execute = () => run(() => api.controlExecute(sid.value!))
const cancel = () => run(() => api.controlCancel(sid.value!))

function close() {
  emit('close')
}
</script>

<template>
  <n-modal
    :show="!!action"
    preset="card"
    :title="`遥控 ${device} ${deviceName} — ${actionCn}`"
    style="width: 600px; max-width: calc(100vw - 24px)"
    :mask-closable="false"
    @update:show="(v: boolean) => !v && close()"
  >
    <n-steps :current="stepIndex" :status="stepStatus" size="small" class="steps">
      <n-step title="五防 · 复诵" />
      <n-step title="监护确认" />
      <n-step title="选择返校" />
      <n-step title="执行" />
    </n-steps>

    <!-- 1. 五防预校验 + 复诵 -->
    <template v-if="!session">
      <div class="section">
        <div class="h">五防校验</div>
        <div v-if="checking" class="dim">校验中…</div>
        <template v-else-if="check">
          <div :class="check.ok ? 'ok' : 'bad'">{{ check.ok ? '✔ 校验通过' : '✘ 五防闭锁，禁止操作' }}</div>
          <div v-if="check.rule" class="rule">
            <span class="dim">规则：</span><span class="mono">{{ check.rule }}</span>
            <div class="dim small">{{ check.rule_desc }}</div>
            <div class="terms">
              <span v-for="t in check.terms" :key="t.term" class="term" :class="t.ok ? 'ok' : 'bad'">
                {{ t.term }} · 实际{{ t.actual }} {{ t.ok ? '✔' : '✘' }}
              </span>
            </div>
          </div>
          <div v-for="v in check.violations" :key="v.text" class="viol">
            <span class="cat">{{ v.category }}</span>{{ v.text }}
          </div>
        </template>
      </div>
      <div class="section">
        <div class="h">复诵设备编号</div>
        <div class="row">
          <n-input
            v-model:value="confirm"
            :placeholder="`请输入 ${device} 确认操作对象`"
            :disabled="!check?.ok"
            @keyup.enter="confirm === device && start()"
          />
          <n-button type="primary" :disabled="!check?.ok || confirm !== device" :loading="busy" @click="start">
            发起{{ actionCn }}
          </n-button>
        </div>
      </div>
    </template>

    <!-- 2. 监护 -->
    <div v-else-if="session.state === 'pending'" class="section">
      <div class="h">监护人确认 <span class="dim small">操作人 {{ session.operator.name }}，监护人须为另一名监护员</span></div>
      <div class="row">
        <n-input v-model:value="supUser" placeholder="监护人用户名" style="width: 150px" />
        <n-input
          v-model:value="supPass"
          type="password"
          placeholder="监护人口令"
          :input-props="{ autocomplete: 'off' }"
          @keyup.enter="supervise"
        />
        <n-button type="primary" :loading="busy" :disabled="!supUser || !supPass" @click="supervise">确认</n-button>
      </div>
      <div v-if="auth.config.demo" class="dim small hint">演示监护员：wang / {{ auth.config.password }}</div>
    </div>

    <!-- 3. 选择 -->
    <div v-else-if="session.state === 'authorized'" class="section">
      <div class="h">选择 <span class="dim small">监护人 {{ session.supervisor?.name }} 已确认</span></div>
      <p class="dim small">向子站发送选择命令（C_DC_NA_1 S/E=1），子站返校成功后 30 秒内须执行。</p>
      <n-button type="primary" :loading="busy" @click="select">选择 {{ device }} {{ actionCn }}</n-button>
    </div>

    <!-- 4. 执行 -->
    <div v-else-if="session.state === 'selected'" class="section">
      <div class="h">执行 <span class="countdown" :class="{ warn: remaining <= 10 }">剩余 {{ remaining }} 秒</span></div>
      <p class="dim small">返校成功。确认无误后执行（S/E=0），超时子站将自动撤销选择。</p>
      <n-button :type="action === 'open' ? 'success' : 'error'" size="large" :loading="busy" @click="execute">
        执行 {{ device }} {{ actionCn }}
      </n-button>
    </div>

    <!-- 结果 -->
    <div v-else class="section">
      <div class="result" :class="session.state === 'done' ? 'ok' : 'bad'">{{ session.state_cn }}</div>
    </div>

    <!-- 过程记录 -->
    <div v-if="session?.steps.length" class="section">
      <div class="h">过程记录</div>
      <div v-for="(s, i) in session.steps" :key="i" class="log">
        <span class="mono dim">{{ fmtTime(s.t) }}</span>
        <span :class="s.ok ? 'ok' : 'bad'">{{ s.ok ? '✔' : '✘' }}</span>
        <span>{{ s.step }}</span>
        <span class="dim">{{ s.detail }}</span>
        <span v-if="s.user" class="dim">· {{ s.user }}</span>
      </div>
    </div>

    <template #footer>
      <div class="footer">
        <n-button v-if="session && !finished" :disabled="busy" @click="cancel">撤销</n-button>
        <n-button @click="close">{{ finished ? '关闭' : session ? '后台保留（会话将超时撤销）' : '取消' }}</n-button>
      </div>
    </template>
  </n-modal>
</template>

<style scoped>
.steps {
  margin-bottom: 16px;
}
.section {
  margin-bottom: 14px;
}
.h {
  font-weight: 600;
  margin-bottom: 6px;
}
.row {
  display: flex;
  gap: 8px;
}
.ok {
  color: var(--ok);
}
.bad {
  color: var(--alarm);
}
.rule {
  margin-top: 6px;
  padding: 8px;
  background: #0f172a;
  border-radius: 4px;
  font-size: 13px;
}
.terms {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}
.term {
  font-size: 12px;
  padding: 1px 6px;
  border: 1px solid currentColor;
  border-radius: 3px;
}
.viol {
  margin-top: 6px;
  font-size: 13px;
  color: var(--alarm);
}
.cat {
  font-size: 11px;
  padding: 0 5px;
  margin-right: 6px;
  border-radius: 3px;
  background: var(--alarm);
  color: #fff;
}
.countdown {
  margin-left: 8px;
  font-family: var(--mono);
  color: var(--ok);
}
.countdown.warn {
  color: var(--alarm);
}
.result {
  font-size: 18px;
  font-weight: 600;
}
.log {
  display: flex;
  gap: 8px;
  font-size: 12px;
  padding: 2px 0;
  flex-wrap: wrap;
}
.hint {
  margin-top: 6px;
}
.footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.dim {
  color: var(--text-dim);
}
.small {
  font-size: 12px;
}
</style>
