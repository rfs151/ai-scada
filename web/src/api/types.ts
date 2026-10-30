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
