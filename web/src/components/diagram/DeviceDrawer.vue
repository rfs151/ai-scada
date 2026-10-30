<script setup lang="ts">
/** 设备详情：台账信息、带电状态、位置与运行方式、间隔量测与信号、最近变位 */
import { computed, ref, watch } from 'vue'
import { NButton, NDrawer, NDrawerContent, NInput, NInputNumber, NPopconfirm, NSelect, useMessage } from 'naive-ui'
import { api } from '../../api/client'
import type { Point, Tag, YxEvent } from '../../api/types'
import { DPI_TEXT, fmtTime, formatValue, qualityFlags } from '../../lib/format'
import type { NodeState, TopoResult } from '../../lib/topology'
import { useAuth } from '../../stores/auth'
import { useModel } from '../../stores/model'
import { useOps } from '../../stores/ops'
import { useRealtime } from '../../stores/realtime'
import ControlDialog from '../control/ControlDialog.vue'

const props = defineProps<{ eqId: string | null; topo: TopoResult | null }>()
const emit = defineEmits<{ close: []; trend: [p: Point] }>()

const model = useModel()
const rt = useRealtime()
const auth = useAuth()
const ops = useOps()
const msg = useMessage()

const TYPE_CN: Record<string, string> = {
  breaker: '断路器',
  disconnector: '隔离刀闸',
  earth_switch: '接地刀闸',
  transformer: '有载调压变压器',
  line: '线路',
}
const NODE_STATE: Record<NodeState, string> = { energized: '带电', dead: '失电', grounded: '接地', conflict: '带电接地' }

const found = computed(() => {
  const st = model.station
  if (!st || !props.eqId) return null
  for (const bay of st.bays) {
    const eq = bay.equipment.find((e) => e.id === props.eqId)
    if (eq) return { eq, bay }
  }
  return null
})

const posPoint = computed(() => (props.eqId ? model.byId.get(`${props.eqId}.POS`) : undefined))
const pos = computed(() => (posPoint.value ? rt.values[posPoint.value.id] : undefined))
const offNormal = computed(() => {
  const n = found.value?.eq.normal
  if (!n || !pos.value || (pos.value.v !== 1 && pos.value.v !== 2)) return false
  return (n === 'closed') !== (pos.value.v === 2)
})

const vlText = computed(() => {
  const f = found.value
  const st = model.station
  if (!f || !st) return ''
  if (f.eq.type === 'transformer') return '110 / 10.5 kV'
  return st.voltage_levels[f.eq.vl]?.name ?? f.eq.vl
})

const bayPoints = computed(() => (found.value ? (model.byBay.get(found.value.bay.id) ?? []) : []))
const analogs = computed(() => bayPoints.value.filter((p) => p.cat === 'YC' || p.cat === 'DD'))
const signals = computed(() => bayPoints.value.filter((p) => p.cat === 'YX' && !p.double))

const events = ref<YxEvent[]>([])
async function loadEvents() {
  const p = posPoint.value
  events.value = p ? await api.yxEvents(8, p.id).catch(() => []) : []
}
watch(() => props.eqId, loadEvents, { immediate: true })
// 设备变位后刷新变位记录（等历史库写入）
watch(
  () => (posPoint.value ? rt.values[posPoint.value.id]?.v : undefined),
  () => window.setTimeout(loadEvents, 1500),
)

const posCls = (v?: number) => (v === undefined ? '' : ['mid', 'open', 'closed', 'mid'][v])

// ---------------------------------------------------------------- 遥控 / 遥调
const ykPoint = computed(() => (props.eqId ? model.byId.get(`${props.eqId}.YK`) : undefined))
const ytPoints = computed(() => bayPoints.value.filter((p) => p.cat === 'YT' && p.ref === props.eqId))
const ctlAction = ref<'open' | 'close' | null>(null)
const pset = ref<number | null>(null)
const adjusting = ref(false)

async function adjust(p: Point, value: string | number) {
  adjusting.value = true
  try {
    const r = await api.adjust(p.id, value)
    if (r.ok) msg.success(`${p.name}：${r.detail}（${r.elapsed_ms} ms）`)
    else msg.error(`${p.name}：${r.detail}`)
  } catch (e) {
    msg.error((e as Error).message)
  } finally {
    adjusting.value = false
  }
}

// ---------------------------------------------------------------- 挂牌
const devTags = computed<Tag[]>(() => (props.eqId ? (ops.tagsByDevice.get(props.eqId) ?? []) : []))
const tagKind = ref<'maint' | 'noclose' | 'ground'>('maint')
const tagNode = ref<string | null>(null)
const tagNote = ref('')
const tagKinds = computed(() => {
  const t = found.value?.eq.type
  return [
    { label: '检修', value: 'maint' },
    ...(t === 'breaker' || t === 'disconnector' ? [{ label: '禁止合闸', value: 'noclose' }] : []),
    { label: '接地线', value: 'ground' },
  ]
})
watch(
  () => props.eqId,
  () => {
    tagKind.value = 'maint'
    const nodes = found.value?.eq.nodes ?? []
    tagNode.value = nodes[nodes.length - 1] ?? null
    tagNote.value = ''
  },
)

async function addTag() {
  try {
    await api.addTag({
      device: props.eqId!,
      kind: tagKind.value,
      node: tagKind.value === 'ground' ? tagNode.value : null,
      note: tagNote.value,
    })
    msg.success('已挂牌')
    tagNote.value = ''
  } catch (e) {
    msg.error((e as Error).message)
  }
}
async function removeTag(t: Tag) {
  try {
    await api.removeTag(t.id)
    msg.success(`已摘除${t.kind_cn}牌`)
  } catch (e) {
    msg.error((e as Error).message)
  }
}

// ---------------------------------------------------------------- 本设备未确认告警
const devAlarms = computed(() => ops.unacked.filter((a) => a.device === props.eqId))
async function ackDevice() {
  if (!auth.can('ack')) {
    auth.showLogin = true
    return
  }
  try {
    await ops.ack(devAlarms.value.map((a) => a.id))
  } catch (e) {
    msg.error((e as Error).message)
  }
}
</script>

<template>
  <n-drawer :show="!!found" :width="420" placement="right" @update:show="(v: boolean) => !v && emit('close')">
    <n-drawer-content v-if="found" :title="`${found.eq.id}  ${found.eq.name}`" closable>
      <dl class="info">
        <dt>设备类型</dt>
        <dd>{{ TYPE_CN[found.eq.type] ?? found.eq.type }}</dd>
        <dt>所属间隔</dt>
        <dd>{{ found.bay.name }}</dd>
        <dt>电压等级</dt>
        <dd>{{ vlText }}</dd>
        <dt>连接节点</dt>
        <dd>
          <span v-for="n in found.eq.nodes" :key="n" class="node" :class="topo?.state(n)">
            {{ n }} · {{ topo ? NODE_STATE[topo.state(n)] : '' }}
          </span>
        </dd>
      </dl>

      <template v-if="posPoint">
        <h4>位置</h4>
        <div class="pos-row">
          <span class="pos" :class="posCls(pos?.v)">{{ pos ? DPI_TEXT[pos.v] : '—' }}</span>
          <span v-for="f in qualityFlags(pos?.q ?? 0)" :key="f.cls" class="tag">{{ f.label }}</span>
          <span class="dim mono small">{{ pos ? fmtTime(pos.t, true) : '' }}</span>
        </div>
        <div class="dim small">
          正常运行方式：{{ found.eq.normal === 'open' ? '分（热备用）' : '合' }}
          <span v-if="offNormal" class="warn">· 偏离正常方式</span>
        </div>
      </template>

      <!-- 遥控 -->
      <template v-if="ykPoint">
        <h4>遥控</h4>
        <div v-if="!auth.can('control')" class="dim small">
          访客只读。<a class="link" @click="auth.showLogin = true">登录调度员账号</a>后可操作。
        </div>
        <div v-else class="btns">
          <n-button type="success" :disabled="pos?.v === 1" @click="ctlAction = 'open'">分闸</n-button>
          <n-button type="error" :disabled="pos?.v === 2" @click="ctlAction = 'close'">合闸</n-button>
          <span class="dim small">选择 → 返校 → 监护 → 执行</span>
        </div>
      </template>

      <!-- 遥调 -->
      <template v-if="ytPoints.length">
        <h4>遥调</h4>
        <div v-if="!auth.can('control')" class="dim small">
          访客只读。<a class="link" @click="auth.showLogin = true">登录调度员账号</a>后可操作。
        </div>
        <template v-else>
          <div v-for="p in ytPoints" :key="p.id" class="btns">
            <template v-if="p.meas === 'TAPCMD'">
              <span class="dim">档位 {{ p.target ? formatValue(model.byId.get(p.target)!, rt.values[p.target]) : '' }}</span>
              <n-popconfirm @positive-click="adjust(p, 'raise')">
                <template #trigger><n-button size="small" :loading="adjusting">升档 ▲</n-button></template>
                确认 {{ found.eq.name }} 升一档？（低压侧电压升高约 1.25%）
              </n-popconfirm>
              <n-popconfirm @positive-click="adjust(p, 'lower')">
                <template #trigger><n-button size="small" :loading="adjusting">降档 ▼</n-button></template>
                确认 {{ found.eq.name }} 降一档？
              </n-popconfirm>
            </template>
            <template v-else-if="p.meas === 'PSET'">
              <n-input-number v-model:value="pset" size="small" :min="0" :max="6" :step="0.5" placeholder="MW" style="width: 120px" />
              <n-button size="small" :disabled="pset === null" :loading="adjusting" @click="adjust(p, pset!)">
                有功设定
              </n-button>
              <span class="dim small">当前限值 {{ p.target ? formatValue(model.byId.get(p.target)!, rt.values[p.target]) : '' }} MW</span>
            </template>
          </div>
        </template>
      </template>

      <!-- 挂牌 -->
      <h4>挂牌</h4>
      <div v-if="devTags.length" class="tags">
        <div v-for="t in devTags" :key="t.id" class="tag-row">
          <span class="tagk" :class="t.kind">{{ t.kind_cn }}</span>
          <span v-if="t.node" class="mono small">@{{ t.node }}</span>
          <span class="dim small">{{ t.created_by }} {{ t.created_at ? fmtTime(t.created_at, true) : '' }} {{ t.note }}</span>
          <n-button v-if="auth.can('tag')" size="tiny" quaternary @click="removeTag(t)">摘牌</n-button>
        </div>
      </div>
      <div v-else class="dim small">无</div>
      <div v-if="auth.can('tag')" class="btns add-tag">
        <n-select v-model:value="tagKind" :options="tagKinds" size="small" style="width: 110px" />
        <n-select
          v-if="tagKind === 'ground'"
          v-model:value="tagNode"
          :options="found.eq.nodes.map((n) => ({ label: n, value: n }))"
          size="small"
          style="width: 110px"
        />
        <n-input v-model:value="tagNote" size="small" placeholder="备注" style="flex: 1; min-width: 80px" />
        <n-button size="small" @click="addTag">挂牌</n-button>
      </div>

      <!-- 未确认告警 -->
      <template v-if="devAlarms.length">
        <h4>未确认告警 <n-button size="tiny" @click="ackDevice">确认</n-button></h4>
        <div v-for="a in devAlarms" :key="a.id" class="small alarm" :class="`l${a.level}`">
          <span class="mono dim">{{ fmtTime(a.ts) }}</span> {{ a.text }}
        </div>
      </template>

      <template v-if="analogs.length">
        <h4>间隔量测 <span class="dim small">点击查看趋势</span></h4>
        <table>
          <tr v-for="p in analogs" :key="p.id" class="clickable" @click="emit('trend', p)">
            <td>{{ p.name.replace(found.bay.name, '') }}</td>
            <td class="r mono">{{ formatValue(p, rt.values[p.id]) }}</td>
            <td class="dim">{{ p.unit }}</td>
          </tr>
        </table>
      </template>

      <template v-if="signals.length">
        <h4>保护及告警信号</h4>
        <div class="signals">
          <span v-for="p in signals" :key="p.id" class="sig" :class="{ on: rt.values[p.id]?.v }">
            {{ p.name.replace(found.bay.name, '') }}
          </span>
        </div>
      </template>

      <template v-if="posPoint">
        <h4>最近变位（SOE）</h4>
        <div v-if="!events.length" class="dim small">暂无记录</div>
        <table v-else>
          <tr v-for="e in events" :key="e.id">
            <td class="mono small">{{ fmtTime(e.dev_t ?? e.recv_t, true) }}</td>
            <td>
              <span class="pos sm" :class="posCls(e.old ?? undefined)">{{ e.old !== null ? DPI_TEXT[e.old] : '?' }}</span>
              →
              <span class="pos sm" :class="posCls(e.v)">{{ DPI_TEXT[e.v] }}</span>
            </td>
          </tr>
        </table>
      </template>
    </n-drawer-content>
  </n-drawer>
  <ControlDialog
    v-if="found"
    :device="found.eq.id"
    :device-name="found.eq.name"
    :action="ctlAction"
    @close="ctlAction = null"
  />
</template>

<style scoped>
.info {
  display: grid;
  grid-template-columns: 72px 1fr;
  gap: 6px 12px;
  margin: 0;
  font-size: 13px;
}
dt {
  color: var(--text-dim);
}
dd {
  margin: 0;
}
h4 {
  margin: 20px 0 8px;
  font-size: 14px;
  font-weight: 600;
}
.node {
  display: inline-block;
  font-family: var(--mono);
  font-size: 12px;
  margin: 0 6px 4px 0;
  padding: 1px 6px;
  border-radius: 3px;
  border: 1px solid var(--deenergized);
  color: var(--text-dim);
}
.node.energized {
  border-color: var(--ok);
  color: var(--ok);
}
.node.grounded {
  border-color: var(--grounded);
  color: var(--grounded);
}
.node.conflict {
  border-color: var(--alarm);
  color: var(--alarm);
}
.pos-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}
.pos {
  display: inline-block;
  min-width: 44px;
  text-align: center;
  padding: 2px 8px;
  border-radius: 3px;
  color: #fff;
  background: var(--deenergized);
}
.pos.sm {
  min-width: 0;
  padding: 0 6px;
  font-size: 12px;
}
.pos.closed {
  background: var(--closed);
}
.pos.open {
  background: var(--open);
}
.pos.mid {
  color: #111;
  background: var(--warn);
}
.tag {
  font-size: 11px;
  padding: 0 5px;
  border: 1px solid var(--alarm);
  color: var(--alarm);
  border-radius: 3px;
}
.warn {
  color: var(--warn);
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
td {
  padding: 4px 6px;
  border-bottom: 1px solid #172238;
}
tr.clickable {
  cursor: pointer;
}
tr.clickable:hover td {
  background: #16213a;
}
.r {
  text-align: right;
}
.signals {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.sig {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 3px;
  border: 1px solid var(--border);
  color: var(--text-dim);
}
.sig.on {
  color: #fff;
  background: var(--alarm);
  border-color: var(--alarm);
}
.btns {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}
.add-tag {
  margin-top: 8px;
}
.link {
  color: var(--accent);
  cursor: pointer;
}
.tag-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.tagk {
  font-size: 12px;
  padding: 1px 8px;
  border-radius: 3px;
  color: #111;
}
.tagk.maint {
  background: var(--warn);
}
.tagk.noclose {
  background: var(--alarm);
  color: #fff;
}
.tagk.ground {
  background: var(--grounded);
}
.alarm {
  padding: 2px 0;
}
.alarm.l1 {
  color: #ef4444;
}
.alarm.l2 {
  color: #f97316;
}
.alarm.l3 {
  color: #eab308;
}
.alarm.l4 {
  color: #60a5fa;
}
.dim {
  color: var(--text-dim);
}
.small {
  font-size: 12px;
}
</style>
