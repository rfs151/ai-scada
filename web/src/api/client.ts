import type {
  Alarm, AuditEntry, AuthConfig, Channel, CheckResult, ControlSession, Frame, History, Pdr, PdrReplay, Point, SimInfo,
  SimState, Station, Tag, User, YxEvent,
} from './types' // prettier-ignore

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

let token: string | null = null
let onUnauthorized: (() => void) | null = null

export function setToken(t: string | null) {
  token = t
}
/** 令牌失效（401）时的回调：清除登录状态并弹出登录框 */
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn
}

async function request<T>(method: string, url: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const headers: Record<string, string> = {}
  if (token) headers.Authorization = `Bearer ${token}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const r = await fetch(url, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
    cache: 'no-store',
  })
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`
    try {
      const j = await r.json()
      if (typeof j.detail === 'string') msg = j.detail
      else if (Array.isArray(j.detail)) msg = j.detail.map((d: { msg: string }) => d.msg).join('；')
    } catch {
      /* 非 JSON 响应 */
    }
    if (r.status === 401 && token) onUnauthorized?.()
    throw new ApiError(r.status, msg)
  }
  return r.json()
}

const get = <T>(url: string, signal?: AbortSignal) => request<T>('GET', url, undefined, signal)
const post = <T>(url: string, body?: unknown) => request<T>('POST', url, body ?? {})

export const api = {
  station: () => get<Station>('/api/model/station'),
  points: () => get<Point[]>('/api/model/points'),
  history: (id: string, start: Date, end?: Date, signal?: AbortSignal) => {
    const q = new URLSearchParams({ start: start.toISOString() })
    if (end) q.set('end', end.toISOString())
    return get<History>(`/api/history/${encodeURIComponent(id)}?${q}`, signal)
  },
  yxEvents: (limit = 300, point?: string) => {
    const q = new URLSearchParams({ limit: String(limit) })
    if (point) q.set('point', point)
    return get<YxEvent[]>(`/api/events/yx?${q}`)
  },
  channels: () => get<Record<string, Channel>>('/api/channels'),
  frames: (channel: string, after?: string, limit = 200) => {
    const q = new URLSearchParams({ limit: String(limit) })
    if (after) q.set('after', after)
    return get<Frame[]>(`/api/channels/${channel}/frames?${q}`)
  },

  // ---- 认证
  authConfig: () => get<AuthConfig>('/api/auth/config'),
  login: (username: string, password: string) =>
    post<{ token: string; user: User; expires_in: number }>('/api/auth/login', { username, password }),

  // ---- 告警
  alarmsActive: () => get<{ unacked: Alarm[]; recent: Alarm[] }>('/api/alarms/active'),
  alarms: (p: { limit?: number; level?: number; unacked?: boolean; before_id?: number } = {}) => {
    const q = new URLSearchParams()
    for (const [k, v] of Object.entries(p)) if (v !== undefined) q.set(k, String(v))
    return get<Alarm[]>(`/api/alarms?${q}`)
  },
  ack: (ids: number[], all = false) => post<{ acked: number[] }>('/api/alarms/ack', { ids, all }),

  // ---- 挂牌
  tags: () => get<Tag[]>('/api/tags'),
  addTag: (t: { device: string; kind: string; node?: string | null; note?: string }) => post<Tag>('/api/tags', t),
  removeTag: (id: number) => request<Tag>('DELETE', `/api/tags/${id}`),

  // ---- 遥控 / 遥调
  controlCheck: (point: string, action: string) =>
    get<CheckResult>(`/api/control/check?${new URLSearchParams({ point, action })}`),
  controlCreate: (point: string, action: string, confirm: string) =>
    post<ControlSession>('/api/control/sessions', { point, action, confirm }),
  controlSupervise: (id: string, username: string, password: string) =>
    post<ControlSession>(`/api/control/sessions/${id}/supervise`, { username, password }),
  controlSelect: (id: string) => post<ControlSession>(`/api/control/sessions/${id}/select`),
  controlExecute: (id: string) => post<ControlSession>(`/api/control/sessions/${id}/execute`),
  controlCancel: (id: string) => post<ControlSession>(`/api/control/sessions/${id}/cancel`),
  adjust: (point: string, value: string | number) =>
    post<{ ok: boolean; detail: string; elapsed_ms?: number }>('/api/control/adjust', { point, value }),

  // ---- 仿真场景 / 事故追忆
  simInfo: () => get<SimInfo>('/api/sim'),
  simState: () => get<SimState | null>('/api/sim/state'),
  simInject: (scenario: string, target?: string | null) =>
    post<{ ok: boolean; detail: string }>('/api/sim/inject', { scenario, target }),
  pdrList: (limit = 50) => get<Pdr[]>(`/api/pdr?limit=${limit}`),
  pdr: (id: number) => get<PdrReplay>(`/api/pdr/${id}`),

  // ---- 审计
  audit: (limit = 300, action?: string) => {
    const q = new URLSearchParams({ limit: String(limit) })
    if (action) q.set('action', action)
    return get<AuditEntry[]>(`/api/audit?${q}`)
  },
}
