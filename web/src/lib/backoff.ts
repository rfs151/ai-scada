/**
 * 断线重连延迟：指数退避 + 抖动（full jitter 的一半），避免服务重启后所有客户端同时重连。
 * attempt 从 0 开始。返回毫秒。
 */
export function reconnectDelay(
  attempt: number,
  { baseMs = 500, maxMs = 15_000, random = Math.random } = {},
): number {
  const exp = Math.min(maxMs, baseMs * 2 ** Math.max(0, attempt))
  return Math.round(exp / 2 + (random() * exp) / 2)
}
