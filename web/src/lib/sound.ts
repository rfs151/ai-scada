/**
 * 告警音响：WebAudio 合成，不依赖音频文件。
 * 浏览器要求用户交互后才能出声：首次点击页面时解锁。
 */
let ctx: AudioContext | null = null

export function unlockAudio() {
  if (!ctx) {
    try {
      ctx = new AudioContext()
    } catch {
      return
    }
  }
  if (ctx.state === 'suspended') void ctx.resume()
}

function tone(freq: number, start: number, dur: number) {
  if (!ctx) return
  const osc = ctx.createOscillator()
  const gain = ctx.createGain()
  osc.type = 'square'
  osc.frequency.value = freq
  gain.gain.setValueAtTime(0.0001, start)
  gain.gain.exponentialRampToValueAtTime(0.08, start + 0.01)
  gain.gain.exponentialRampToValueAtTime(0.0001, start + dur)
  osc.connect(gain).connect(ctx.destination)
  osc.start(start)
  osc.stop(start + dur + 0.02)
}

/** 事故：急促高低音交替；异常：两声提示 */
export function alarmSound(level: number) {
  if (!ctx || ctx.state !== 'running') return
  const t = ctx.currentTime
  if (level === 1) {
    for (let i = 0; i < 6; i++) tone(i % 2 ? 660 : 990, t + i * 0.18, 0.15)
  } else {
    tone(880, t, 0.12)
    tone(880, t + 0.22, 0.12)
  }
}
