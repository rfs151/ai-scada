import { describe, expect, it } from 'vitest'
import { reconnectDelay } from './backoff'

describe('reconnectDelay', () => {
  it('grows exponentially within [exp/2, exp]', () => {
    expect(reconnectDelay(0, { random: () => 0 })).toBe(250)
    expect(reconnectDelay(0, { random: () => 1 })).toBe(500)
    expect(reconnectDelay(3, { random: () => 1 })).toBe(4000)
  })

  it('is capped at maxMs', () => {
    expect(reconnectDelay(50, { random: () => 1 })).toBe(15_000)
    expect(reconnectDelay(50, { random: () => 0 })).toBe(7_500)
  })
})
