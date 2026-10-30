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
