/**
 * 110kV 示范变电站一次接线图布局（图模分离）。
 *
 * 这里只描述"画在哪里、绑定哪个模型对象"：
 *  - 连接线绑定连接节点（ConnectivityNode），颜色由拓扑分析决定
 *  - 图元绑定设备编号，状态由双点遥信决定
 *  - 量测标注 / 潮流箭头绑定点号
 * 一致性由 layout.test.ts 对照 config/station.yaml 与点表校验，模型改动后图纸缺漏会在 CI 中暴露。
 */

export interface WireDef {
  node: string
  d: string
  bus?: boolean
}
export interface LineDef {
  eq: string
  d: string
}
export type LabelPos = 'l' | 'r' | 't' | 'b'
export interface SymDef {
  eq: string
  x: number
  y: number
  /** 水平放置 */
  h?: boolean
  /** 接地刀伸出方向：1 向右，-1 向左 */
  side?: 1 | -1
  label?: LabelPos
}
export interface MeasDef {
  pt: string
  x: number
  y: number
  label: string
  anchor?: 'start' | 'end' | 'middle'
}
/** 潮流箭头：沿竖直线段，正方向为 y1 → y2；node 用于判断该段是否带电 */
export interface FlowDef {
  pt: string
  node: string
  x: number
  y1: number
  y2: number
}
export interface TermDef {
  kind: 'source' | 'load' | 'pv' | 'capacitor' | 'dot'
  node: string
  x: number
  y: number
  label?: string
}
export interface TextDef {
  x: number
  y: number
  text: string
  anchor?: 'start' | 'end' | 'middle'
  cls?: string
  bay?: string
}

export interface Layout {
  width: number
  height: number
  wires: WireDef[]
  lines: LineDef[]
  symbols: SymDef[]
  meas: MeasDef[]
  flows: FlowDef[]
  terms: TermDef[]
  texts: TextDef[]
}

const L: Layout = { width: 1400, height: 840, wires: [], lines: [], symbols: [], meas: [], flows: [], terms: [], texts: [] }

const wire = (node: string, d: string, bus = false) => L.wires.push({ node, d, ...(bus ? { bus } : {}) })
const V = (x: number, y1: number, y2: number) => `M${x} ${y1}V${y2}`
const H = (y: number, x1: number, x2: number) => `M${x1} ${y}H${x2}`

/** 竖排量测：从 y0 起每行 18px */
function measCol(x: number, y0: number, anchor: 'start' | 'end', items: [string, string][]) {
  items.forEach(([pt, label], i) => L.meas.push({ pt, x, y: y0 + i * 18, label, anchor }))
}

// ------------------------------------------------------------------ 110kV 进线（s：设备编号/接地刀朝向，量测在另一侧）
const Y_BRIDGE = 345

function lineIn(n: 1 | 2, x: number, s: 1 | -1, source: string, name: string) {
  const out = s > 0 ? 'end' : 'start'
  const mx = x - s * 14
  const sideLbl: LabelPos = s > 0 ? 'l' : 'r'

  L.terms.push({ kind: 'source', node: 'GRID', x, y: 50, label: source })
  L.lines.push({ eq: `L${n}`, d: V(x, 50, 130) })
  wire(`L${n}_T`, V(x, 130, 150))
  L.symbols.push({ eq: `10${n}67`, x, y: 130, side: s })
  L.symbols.push({ eq: `10${n}6`, x, y: 170, label: sideLbl })
  wire(`L${n}_C`, V(x, 190, 217))
  L.symbols.push({ eq: `10${n}`, x, y: 235, label: sideLbl })
  wire(`L${n}_B`, V(x, 253, 275))
  L.symbols.push({ eq: `10${n}1`, x, y: 295, label: sideLbl })
  wire(`HB${n}`, V(x, 315, 355))
  L.terms.push({ kind: 'dot', node: `HB${n}`, x, y: Y_BRIDGE })

  L.texts.push({ x: mx, y: 66, text: name, anchor: out, cls: 'bay-name', bay: `L${n}` })
  measCol(mx, 86, out, [[`L${n}.P`, 'P'], [`L${n}.Q`, 'Q'], [`L${n}.I`, 'I'], [`L${n}.UAB`, 'Uab']])
  L.flows.push({ pt: `L${n}.P`, node: `L${n}_T`, x, y1: 64, y2: 126 })
}

lineIn(1, 420, 1, '220kV 城东变', '110kV 进线一')
lineIn(2, 980, -1, '220kV 城西变', '110kV 进线二')

// ------------------------------------------------------------------ 内桥
wire('HB1', H(Y_BRIDGE, 420, 500))
L.symbols.push({ eq: '1001', x: 520, y: Y_BRIDGE, h: true, label: 't' })
wire('B100_A', H(Y_BRIDGE, 540, 682))
L.symbols.push({ eq: '100', x: 700, y: Y_BRIDGE, h: true, label: 't' })
wire('B100_B', H(Y_BRIDGE, 718, 860))
L.symbols.push({ eq: '1002', x: 880, y: Y_BRIDGE, h: true, label: 't' })
wire('HB2', H(Y_BRIDGE, 900, 980))
L.texts.push({ x: 700, y: Y_BRIDGE + 34, text: '110kV 内桥', anchor: 'middle', cls: 'bay-name', bay: 'B100' })

// ------------------------------------------------------------------ 主变
function transformer(n: 1 | 2, x: number, s: 1 | -1, name: string) {
  const out = s > 0 ? 'end' : 'start'
  const mx = x - s * 34
  const sideLbl: LabelPos = s > 0 ? 'r' : 'l'
  const T = `T${n}`

  L.symbols.push({ eq: `110${n}`, x, y: 375, label: sideLbl })
  wire(`${T}_H`, V(x, 395, 408))
  L.symbols.push({ eq: T, x, y: 444, label: sideLbl })
  wire(`${T}_L`, V(x, 480, 514))
  L.symbols.push({ eq: `90${n}`, x, y: 540, label: sideLbl })
  wire(`BUS${n}`, V(x, 566, 580))

  L.texts.push({ x: mx, y: 412, text: name, anchor: out, cls: 'bay-name', bay: T })
  measCol(mx, 432, out, [[`${T}.TAP`, '档位'], [`${T}.TOIL`, '油温'], [`${T}.LOAD`, '负载率']])
  measCol(x - s * 14, 500, out, [[`${T}.LP`, 'P'], [`${T}.LQ`, 'Q'], [`${T}.LI`, 'I']])
  L.flows.push({ pt: `${T}.LP`, node: `${T}_L`, x, y1: 484, y2: 512 })
}

transformer(1, 420, 1, '1号主变')
transformer(2, 980, -1, '2号主变')

// ------------------------------------------------------------------ 10kV 母线与分段
const Y_BUS = 580
wire('BUS1', H(Y_BUS, 80, 640), true)
wire('BUS1', H(Y_BUS, 640, 674))
L.symbols.push({ eq: '900', x: 700, y: Y_BUS, h: true, label: 't' })
wire('BUS2', H(Y_BUS, 726, 760))
wire('BUS2', H(Y_BUS, 760, 1320), true)

L.texts.push({ x: 84, y: 524, text: '10kV I 段母线', anchor: 'start', cls: 'bay-name', bay: 'BUS1' })
measCol(84, 544, 'start', [['BUS1.UAB', 'Uab'], ['BUS1.F', 'f']])
L.texts.push({ x: 1316, y: 524, text: '10kV II 段母线', anchor: 'end', cls: 'bay-name', bay: 'BUS2' })
measCol(1316, 544, 'end', [['BUS2.UAB', 'Uab'], ['BUS2.F', 'f']])

// ------------------------------------------------------------------ 10kV 出线（中置柜手车断路器 + 出线接地刀）
function feeder(bay: string, bus: 1 | 2, x: number, cb: string, name: string, kind: 'load' | 'pv' | 'capacitor') {
  wire(`BUS${bus}`, V(x, Y_BUS, 604))
  L.symbols.push({ eq: cb, x, y: 630, label: 'r' })
  L.texts.push({ x, y: 822, text: name, anchor: 'middle', cls: 'bay-name', bay })

  if (kind === 'capacitor') {
    wire(`${bay}_N`, V(x, 656, 700))
    L.terms.push({ kind, node: `${bay}_N`, x, y: 700 })
    measCol(x + 14, 686, 'start', [[`${bay}.Q`, 'Q'], [`${bay}.I`, 'I']])
    return
  }

  const out = `${bay}_O`
  const end = kind === 'pv' ? `${bay}_PV` : `${bay}_LD`
  wire(out, V(x, 656, 690))
  L.symbols.push({ eq: `${cb}7`, x, y: 672, side: -1 })
  L.lines.push({ eq: bay, d: V(x, 690, 770) })
  L.terms.push({ kind, node: end, x, y: 770 })
  measCol(x + 12, 704, 'start', [[`${bay}.P`, 'P'], [`${bay}.Q`, 'Q'], [`${bay}.I`, 'I']])
  // 光伏线路以送出（流向母线）为正
  L.flows.push(kind === 'pv' ? { pt: `${bay}.P`, node: out, x, y1: 766, y2: 694 } : { pt: `${bay}.P`, node: out, x, y1: 694, y2: 766 })
}

feeder('F01', 1, 180, '911', '工业园线', 'load')
feeder('F02', 1, 340, '912', '锦苑线', 'load')
feeder('C1', 1, 540, '913', '1号电容器', 'capacitor')
feeder('F03', 2, 860, '921', '商业中心线', 'load')
feeder('F04', 2, 1060, '922', '光伏并网线', 'pv')
feeder('F05', 2, 1220, '923', '滨河线', 'load')

export const layout: Layout = L
