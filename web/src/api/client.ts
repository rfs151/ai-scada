import type { Channel, Frame, History, Point, Station, YxEvent } from './types'

async function get<T>(url: string, signal?: AbortSignal): Promise<T> {
  const r = await fetch(url, { signal, cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

export const api = {
  station: () => get<Station>('/api/model/station'),
  points: () => get<Point[]>('/api/model/points'),
  history: (id: string, start: Date, end?: Date, signal?: AbortSignal) => {
    const q = new URLSearchParams({ start: start.toISOString() })
    if (end) q.set('end', end.toISOString())
    return get<History>(`/api/history/${encodeURIComponent(id)}?${q}`, signal)
  },
  yxEvents: (limit = 300) => get<YxEvent[]>(`/api/events/yx?limit=${limit}`),
  channels: () => get<Record<string, Channel>>('/api/channels'),
  frames: (channel: string, after?: string, limit = 200) => {
    const q = new URLSearchParams({ limit: String(limit) })
    if (after) q.set('after', after)
    return get<Frame[]>(`/api/channels/${channel}/frames?${q}`)
  },
}
