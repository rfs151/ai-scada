export type Cat = 'YC' | 'YX' | 'YK' | 'YT' | 'DD'

export interface Limits {
  hihi?: number
  hi?: number
  lo?: number
  lolo?: number
}

export interface Point {
  id: string
  name: string
  cat: Cat
  ioa: number
  bay: string
  ref: string
  meas: string
  unit: string
  deadband: number
  limits: Limits | null
  double: boolean
  target: string | null
  decimals: number
}

export interface Equipment {
  id: string
  type: string
  name: string
  vl: string
  nodes: string[]
  /** 开关正常运行方式 */
  normal?: 'open' | 'closed'
}

export interface Bay {
  id: string
  name: string
  kind: string
  vl: string
  equipment: Equipment[]
}

export interface Station {
  id: string
  name: string
  ca: number
  voltage_levels: Record<string, { name: string; vn_kv: number }>
  /** 电源点连接节点 */
  sources: string[]
  /** 连接节点 → 电压等级 */
  nodes: Record<string, string>
  bays: Bay[]
}

/** 实时值：v 值，q 品质位（IEC 104 QDS），t 时标（ms） */
export interface RtValue {
  v: number
  q: number
  t: number
}

export interface YxEvent {
  id?: number
  p: string
  v: number
  old: number | null
  dev_t: number | null
  recv_t: number
  cot: number
  name?: string
  bay?: string
  double?: boolean
}

export interface Frame {
  id: string
  dir: 'tx' | 'rx'
  hex: string
  text: string
  t: number
}

export type Channel = Record<string, string | number>

export interface History {
  mode: 'raw' | 'agg'
  bucket_s?: number
  start: number
  end: number
  data: number[][]
}

// ------------------------------------------------------------ P3：用户、告警、挂牌、遥控、审计

export interface User {
  username: string
  name: string
  role: 'viewer' | 'operator' | 'supervisor' | 'admin'
  role_cn: string
  perms: string[]
}

export interface AuthConfig {
  demo: boolean
  password?: string
  accounts?: { username: string; name: string; role: string; role_cn: string }[]
}

/** 告警：level 1 事故 / 2 异常 / 3 越限 / 4 变位 / 5 告知 */
export interface Alarm {
  id: number
  ts: number
  level: number
  level_cn: string
  kind: string
  text: string
  point: string | null
  bay: string | null
  device: string | null
  value: number | null
  suppressed: boolean
  ack_by: string | null
  ack_ts: number | null
}

export interface Tag {
  id: number
  device: string
  kind: 'maint' | 'noclose' | 'ground'
  kind_cn: string
  node: string | null
  note: string
  created_by: string
  created_at: number | null
}

export interface CheckResult {
  ok: boolean
  violations: { category: string; text: string }[]
  rule: string | null
  rule_desc: string | null
  terms: { term: string; actual: string; ok: boolean }[]
}

export type SessionState = 'pending' | 'authorized' | 'selected' | 'done' | 'rejected' | 'failed' | 'cancelled' | 'expired'

export interface ControlSession {
  id: string
  point: string
  device: string
  device_name: string
  action: 'open' | 'close'
  action_cn: string
  state: SessionState
  state_cn: string
  operator: User
  supervisor: User | null
  remaining_s: number | null
  check: CheckResult | null
  steps: { t: number; step: string; ok: boolean; detail: string; user: string | null }[]
}

export interface AuditEntry {
  id: number
  ts: number
  username: string | null
  role: string | null
  action: string
  target: string
  ok: boolean
  detail: Record<string, unknown> | null
  ip: string | null
}

// ------------------------------------------------------------ P4：仿真场景、事故追忆

export interface Scenario {
  id: string
  name: string
  desc: string
  target: 'feeder' | 'line' | 'trafo' | null
  restore?: boolean
}

export interface SimState {
  t: number
  faults: { bay: string; name: string; kind: 'short' | 'ground' | 'trafo'; permanent: boolean }[]
  line_lost: string[]
  bzt: Record<string, { name: string; charged: boolean; charging_s: number | null; acting: boolean }>
  reclose: Record<string, number>
  events: { t: number; text: string }[]
  grid_vm: number
  outage_s: number
}

export interface SimInfo {
  scenarios: Scenario[]
  targets: Record<string, { id: string; name: string }[]>
  state: SimState | null
  auto_reset: AutoResetInfo
}

export interface Pdr {
  id: number
  ts: number
  title: string
  bay: string | null
  pre_s: number
  post_s: number
}

export interface PdrReplay {
  pdr: Pdr
  start: number
  end: number
  /** 起始断面：点号 → [值, 品质] */
  init: Record<string, [number, number]>
  /** 遥测变化：[时标, 点号, 值, 品质]，按时间排序 */
  analog: [number, string, number, number][]
  events: YxEvent[]
}

// ------------------------------------------------------------ P5：运行报表

export interface LoadStats {
  max: number | null
  max_t: number | null
  min: number | null
  min_t: number | null
  avg: number | null
  load_factor: number | null
  peak_valley: number | null
}

export interface Report {
  start: number
  end: number
  bucket_s: number
  load: LoadStats
  import: LoadStats
  curve: { t: number; import: number | null; load: number | null; pv: number | null }[]
  voltage: {
    point: string
    name: string
    lo: number
    hi: number
    rate: number | null
    minutes: number
    over: number
    under: number
    outage: number
    max: number | null
    max_t: number | null
    min: number | null
    min_t: number | null
  }[]
  transformers: {
    bay: string
    name: string
    load_max: number | null
    load_max_t: number | null
    load_avg: number | null
    oil_max: number | null
    oil_max_t: number | null
    tap_ops: number
    energy_kwh: number | null
  }[]
  feeders: {
    bay: string
    name: string
    kind: string
    i_max: number | null
    i_max_t: number | null
    i_limit: number | null
    p_max: number | null
    p_avg: number | null
    energy_kwh: number | null
    trips: number
  }[]
  alarms: Record<string, number>
  operations: Record<string, { ok: number; fail: number }>
  pdr: number
}

export interface AutoResetInfo {
  enabled: boolean
  interval_min?: number
  idle_min?: number
  next_at?: number | null
  last?: { t: number; tags_removed: number; alarms_acked: number } | null
}
