export interface ComponentHealth {
  status: 'ok' | 'down'
  latency_ms: number
  error?: string
  [k: string]: unknown
}

export interface HealthReport {
  status: 'ok' | 'degraded'
  version: string
  server_time: string
  uptime_s: number
  components: Record<string, ComponentHealth>
  workers: { db_ready: boolean; historian: Record<string, number>; hub: Record<string, number> } | null
}

/** 就绪探针：503 也会返回 JSON 报告，只有网络/网关错误才抛异常 */
export async function fetchHealth(signal?: AbortSignal): Promise<HealthReport> {
  const r = await fetch('/api/health', { signal, cache: 'no-store' })
  if (r.status !== 200 && r.status !== 503) throw new Error(`HTTP ${r.status}`)
  return r.json()
}
