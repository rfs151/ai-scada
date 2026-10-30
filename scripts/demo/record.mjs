/**
 * 演示录制：按"5 分钟演示脚本"自动操作界面，输出带字幕的演示视频和 README 截图。
 *
 *   cd scripts/demo && npm i && npx playwright install chromium
 *   node record.mjs [站点地址，默认 http://127.0.0.1:18088]
 *
 * 输出：out/demo.webm（有 ffmpeg 时另转 out/demo.mp4）、../../docs/images/*.png
 * 会登录演示账号并真实执行故障注入、遥控；结束时恢复正常运行方式。
 */
import { execFileSync } from 'node:child_process'
import { mkdirSync, renameSync } from 'node:fs'
import { chromium } from 'playwright'

const BASE = process.argv[2] ?? 'http://127.0.0.1:18088'
const PASSWORD = process.env.DEMO_PASSWORD ?? 'scada@2024'
const SIZE = { width: 1440, height: 900 }
const IMG = new URL('../../docs/images/', import.meta.url).pathname
const OUT = new URL('./out/', import.meta.url).pathname
mkdirSync(IMG, { recursive: true })
mkdirSync(OUT, { recursive: true })

const browser = await chromium.launch({ args: ['--no-proxy-server', '--autoplay-policy=no-user-gesture-required'] })
const ctx = await browser.newContext({ viewport: SIZE, recordVideo: { dir: OUT, size: SIZE }, locale: 'zh-CN' })
const page = await ctx.newPage()
const modal = page.locator('.n-modal')
const wait = (ms) => page.waitForTimeout(ms)

/** 底部字幕 */
async function caption(text, ms = 2500) {
  await page.evaluate((t) => {
    let el = document.getElementById('demo-caption')
    if (!el) {
      el = document.createElement('div')
      el.id = 'demo-caption'
      Object.assign(el.style, {
        position: 'fixed', left: '50%', bottom: '64px', transform: 'translateX(-50%)', zIndex: 99999,
        background: 'rgba(8,12,24,.88)', color: '#fff', padding: '10px 22px', borderRadius: '8px',
        font: '600 20px "Microsoft YaHei","WenQuanYi Micro Hei",sans-serif', border: '1px solid #3b82f6',
        maxWidth: '80vw', textAlign: 'center', pointerEvents: 'none', transition: 'opacity .3s',
      })
      document.body.appendChild(el)
    }
    el.textContent = t
    el.style.opacity = t ? '1' : '0'
  }, text)
  if (ms) await wait(ms)
}
const shot = (name) => page.screenshot({ path: `${IMG}${name}.png` })
const sym = (id) => page.locator(`g.sym:has(text:text-is("${id}"))`).first()

async function api(path, body, token) {
  const r = await page.request.post(BASE + path, {
    data: body, headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  return r.json()
}

async function login(username) {
  await page.click('button.login')
  await modal.getByPlaceholder('用户名').fill(username)
  await modal.getByPlaceholder('口令').fill(PASSWORD)
  await modal.getByPlaceholder('口令').press('Enter')
  await wait(1000)
}

/** 完整遥控流程：五防 + 复诵 → 监护 → 选择 → 执行 */
async function remoteControl(device, action) {
  await sym(device).click()
  await wait(800)
  await page.locator(`.n-drawer button:has-text("${action}")`).click()
  await wait(1200)
  await modal.getByPlaceholder(`请输入 ${device} 确认操作对象`).pressSequentially(device, { delay: 120 })
  await caption('① 五防校验通过，复诵设备编号', 1500)
  await modal.locator(`button:has-text("发起${action}")`).click()
  await wait(800)
  await modal.getByPlaceholder('监护人口令').pressSequentially(PASSWORD, { delay: 40 })
  await caption('② 监护人（另一名监护员）输入口令确认', 1200)
  await modal.locator('button:has-text("确认")').click()
  await wait(900)
  await caption('③ 选择：子站返校成功，30 秒内须执行', 1200)
  await modal.locator(`button:has-text("选择 ${device}")`).click()
  await wait(1800)
  await shot('control')
  await caption('④ 执行', 800)
  await modal.locator(`button:has-text("执行 ${device}")`).click()
  await wait(2500)
}

try {
  // ---- 准备：恢复正常运行方式
  const { token } = await api('/api/auth/login', { username: 'zhang', password: PASSWORD })
  await api('/api/sim/inject', { scenario: 'reset' }, token)
  await api('/api/alarms/ack', { ids: [], all: true }, token)

  await page.goto(`${BASE}/diagram`)
  await wait(3000)
  await caption('110kV 示范变电站 SCADA：自研 IEC 104 规约 · 潮流仿真子站 · 前置采集 · Web 人机界面', 4000)
  await caption('一次接线图：拓扑着色（带电 / 失电 / 接地）、潮流方向、实时量测', 3500)
  await shot('diagram')

  // ---- 故障：F02 永久故障
  await login('zhang')
  await page.click('button:has-text("故障注入")')
  await wait(1500)
  await caption('注入故障：10kV 锦苑线永久性相间短路', 2500)
  await page.locator('.card', { hasText: '出线永久故障' }).locator('button:has-text("注入")').click()
  await caption('过流保护 0.3s 跳闸 → 1s 后重合闸 → 合于故障，后加速再次跳闸', 4500)
  await shot('fault')
  await page.locator('.n-drawer-header__close').first().click().catch(() => {})
  await wait(600)
  await page.locator('.panel .bar').click()
  await caption('告警窗：事故告警、音响、设备闪烁；线路失电变灰', 4000)
  await shot('alarms')
  await page.locator('.panel .bar').click()

  // ---- 事故追忆
  await caption('事故追忆自动生成，点击回放', 2000)
  await page.locator('.n-notification button:has-text("回放")').first().click()
  await wait(2500)
  await caption('PDR 回放：从事故前断面开始，按 SOE 毫秒时标逐条重放', 3000)
  // 逐事件单步，字幕取当前高亮的 SOE 条目（相对时间 + 信号 + 状态）
  for (let i = 0; i < 8; i++) {
    await page.keyboard.press('ArrowRight')
    await wait(200)
    const cur = page.locator('.soe .ev.cur')
    const [t, name, v] = await Promise.all(['.t', '.n', '.v'].map((c) => cur.locator(c).innerText()))
    await caption(`T${t}  ${name} ${v}`, 1300)
    if (i === 6) await shot('pdr')
  }

  // ---- 消除故障、五防、遥控恢复送电
  await page.goto(`${BASE}/diagram`)
  await wait(2500)
  await api('/api/sim/inject', { scenario: 'clear_faults' }, token)
  await caption('抢修完成，故障消除。下面遥控恢复送电', 2500)
  await sym('1016').click()
  await wait(800)
  await page.locator('.n-drawer button:has-text("分闸")').click()
  await wait(1500)
  await caption('五防：断路器 101 在合位，禁止拉开刀闸 1016（防带负荷拉刀闸）', 3500)
  await shot('interlock')
  await modal.locator('button:has-text("取消")').click()
  await page.keyboard.press('Escape')
  await wait(600)
  await remoteControl('912', '合闸')
  await caption('遥控合闸成功，锦苑线恢复供电', 2500)
  await modal.locator('button:has-text("关闭")').click()
  await page.keyboard.press('Escape')
  await wait(800)
  await page.locator('.panel .bar button:has-text("全部确认")').click()
  await caption('值班员确认告警，设备停止闪烁', 2500)

  // ---- 报表、报文
  await page.goto(`${BASE}/reports`)
  await wait(3000)
  await caption('运行报表：负荷曲线、电压合格率、主变与出线统计，一键导出 Excel', 4000)
  await shot('reports')
  await page.goto(`${BASE}/frames`)
  await wait(3000)
  await caption('报文监视：IEC 104 报文实时解析（APCI / ASDU / 品质 / 时标）', 4000)
  await shot('frames')
  await page.goto(`${BASE}/diagram`)
  await wait(1500)
  await caption('感谢观看 · github.com/rfs151/ai-scada', 3000)
} finally {
  const { token } = await api('/api/auth/login', { username: 'zhang', password: PASSWORD }).catch(() => ({}))
  if (token) await api('/api/sim/inject', { scenario: 'reset' }, token).catch(() => {})
  const video = page.video()
  await ctx.close()
  await browser.close()
  const webm = `${OUT}demo.webm`
  renameSync(await video.path(), webm)
  try {
    execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', webm, '-c:v', 'libx264', '-crf', '28', '-preset', 'slow',
      '-pix_fmt', 'yuv420p', '-movflags', '+faststart', `${OUT}demo.mp4`])
    console.log(`视频：${OUT}demo.mp4`)
  } catch {
    console.log(`视频：${webm}（未找到 ffmpeg，未转 mp4）`)
  }
  console.log(`截图：${IMG}`)
}
