# PowerGrid SCADA Demo

电力系统 SCADA 演示系统：仿真 110kV 变电站经 **IEC 60870-5-104** 规约接入，主站完成采集、实时处理、告警/SOE、遥控（选择-返校-执行 + 五防）、一次接线图拓扑着色与事故追忆。

> 完整设计见 [docs/开发方案.md](docs/开发方案.md)，点表见 [docs/点表.csv](docs/点表.csv)。
> 进度：**P0 骨架** ✅ · **P1 数据链路** ✅ · **P2 接线图** ✅ · **P3 告警/遥控/五防** ✅ · **P4 故障场景/事故追忆** ✅ · **P5 报表/自动复位/演示** ✅ · **P6 数据校核/负荷预测/事故简报** ✅

![接线图](docs/images/diagram.png)

| 故障注入：永久故障后加速跳闸 | 事故追忆：按 SOE 时标逐事件回放 |
|---|---|
| ![故障](docs/images/fault.png) | ![PDR](docs/images/pdr.png) |
| **五防闭锁：防带负荷拉刀闸** | **遥控：选择返校 → 监护 → 执行** |
| ![五防](docs/images/interlock.png) | ![遥控](docs/images/control.png) |
| **运行报表：负荷曲线、电压合格率** | **报文监视：IEC 104 实时解析** |
| ![报表](docs/images/reports.png) | ![报文](docs/images/frames.png) |

## 演示

5 分钟演示路线（与 `scripts/demo/record.mjs` 自动录制的视频一致）：

1. 接线图：拓扑着色、潮流方向、实时量测
2. 登录 → 故障注入「出线永久故障」：保护跳闸 → 重合 → 后加速再跳，告警窗事故音响、线路变灰
3. 点通知「回放」：事故追忆按 SOE 毫秒时标单步重放
4. 「消除故障」后遥控：先试拉 1016 刀闸被五防闭锁，再走 912 合闸（复诵 → 监护 → 选择返校 → 执行）恢复送电
5. 确认告警、运行报表导出 Excel、报文监视看 104 报文解析

演示账号（`DEMO_MODE=true` 时登录框列出）：张工 / 李工（调度员）、王工（监护员）、admin，口令 `scada@2024`。
公开演示站每 60 分钟自动恢复正常运行方式（有人操作时推迟到空闲 10 分钟后），由 `AUTO_RESET_MIN` 配置。

录制演示视频与截图：

```bash
cd scripts/demo && npm i && npx playwright install chromium
node record.mjs http://127.0.0.1:18088   # 输出 out/demo.mp4 与 docs/images/*.png
```

## 架构

```
浏览器 ──HTTPS/WSS──► gateway (Caddy + Vue3)
                         │ /api /ws
                         ▼
                        api (FastAPI) ── 历史库写入 ──► TimescaleDB (历史/模型/SOE)
                         ▲  推送中心
                         │
                       Redis  实时库 rt:val · 变化流 rt:chg · 变位流 ev:yx · 报文流 fep:frames
                         ▲
                        fep  前置采集：IEC 104 主站
                         ▲ IEC 104 (TCP 2404，仅内网)
                      rtu-sim  仿真子站：pandapower 潮流 + IEC 104 子站
```

| 目录 | 说明 |
|---|---|
| `config/station.yaml` | 厂站模型（单一事实来源）：主接线、设备参数、负荷特性；点表由它按规则生成 |
| `packages/scadalib` | 共享库：**自研 IEC 104 规约栈**、厂站模型与点表生成 |
| `services/rtu-sim` | 仿真子站：潮流计算、负荷曲线、设备动作过程、间隔联锁、104 子站 |
| `services/fep` | 前置采集：104 主站、品质处理、变化检测、SOE、报文监视、控制命令转发 |
| `services/api` | SCADA 服务：迁移、历史库写入、实时推送、REST |
| `web` | Vue 3 人机界面；`web/Dockerfile` 构建网关镜像 |

## 技术要点

**IEC 104 规约栈（`packages/scadalib/scadalib/iec104`，纯 asyncio，无第三方依赖）**
- APCI I/S/U 帧、15 位序号回绕；k=12 / w=8 流量控制窗口；t1/t2/t3 定时器（确认超时断链、S 帧延迟确认、空闲 TESTFR）
- ASDU：单/双点遥信（含 CP56Time2a SOE）、短浮点遥测、电度、双点遥控、升降、设点、总召、电度召唤、对时；SQ=1 解码
- 子站：选择-执行（SBO）及超时/撤销、值不一致拒绝、否定确认（COT 44~47）
- 主站：断线重连、STARTDT → 对时 → 总召 → 电度召唤，周期召唤，命令等待激活确认/激活终止
- **与 lib60870-C（c104）双向互操作测试**：c104 主站 ↔ 自研子站、自研主站 ↔ c104 子站

**仿真子站**
- 110kV 内桥 + 10kV 单母分段；pandapower 交流潮流，开关分合真实改变拓扑
- 工业/居民/商业典型日负荷曲线 + OU 随机过程；光伏晴空曲线 × 云量；60 倍速仿真时钟
- 刀闸 3s 行程（双点"中间态"）、OLTC 5s/档、IEC 60076-7 顶层油温模型、电度积分
- RTU 时钟带初始偏差，主站对时后校正；间隔层联锁（断路器合位禁止操作同间隔刀闸）

**一次接线图（`web/src/diagram`、`web/src/lib/topology.ts`）**
- 图模分离：布局文件只存坐标和模型引用（设备编号 / 连接节点 / 点号），`layout.test.ts` 对照模型与点表做图模一致性校验
- 拓扑着色：按 CIM 连接节点建图，从电源点 BFS 求带电范围；接地范围经开关、线路传递但不穿越主变；带电合接地刀标红闪烁；开关中间态/不定态按不导通处理
- 配色：110kV 朱红、10kV 绛红（Q/GDW 624），失电灰；开关红合绿分，10kV 手车断路器、主变 OLTC 符号
- 潮流箭头按有功方向与大小动画；量测越限变色、品质 NT/IV 灰显；变位闪烁；点击设备看台账/位置/运行方式/SOE，点击量测看趋势

**告警（`services/api/app/alarm`）**
- 五级：事故 / 异常 / 越限 / 变位 / 告知（告知自动确认）；全局告警窗分级计数、未确认闪烁、事故/异常音响，确认后设备停闪
- 越限：上限/上上限/下限/下下限，回差 = max(死区, 1% 限值)，越限与恢复均需持续 10s（越上上限立即告警），避免负荷在限值附近反复告警
- 母线失压闭锁频率/零序告警；断路器分闸分类：遥控变位（带操作人/监护人）、保护动作 ±5s 内为事故跳闸、其余为非遥控变位
- 检修挂牌间隔的告警自动抑制；光字牌按间隔点亮保护/告警信号

**遥控与五防（`services/api/app/control`）**
- 流程：五防校验 + 复诵设备编号 → 监护人（另一名监护员账号口令）确认 → 选择返校 → 30s 内执行；任一步可撤销，超时自动撤销
- 五防：规则库表达式（`config/interlock.yaml`，如 `901=分 & 101=分 & 100=分`）+ 拓扑防误（模拟操作后判断带电合接地刀、接地线所在节点须无电）+ 挂牌闭锁（检修 / 禁止合闸）；选择和执行前各复核一次
- 遥调：主变升降档、光伏有功设定；临时接地线挂牌参与拓扑着色
- 认证：PBKDF2 口令 + JWT；访客只读 / 调度员 / 监护员 / 管理员；登录限流；登录、遥控每一步、挂牌、告警确认全部写审计日志

**站内自动装置与故障场景（`services/rtu-sim/rtusim/automation.py`）**
- 出线过流保护 0.3s、一次重合闸 1s（动作后 15s 充电）、重合或手合于故障后加速 0.04s 跳闸
- 主变差动 + 重瓦斯：跳主变各侧断路器（按内桥接线跳闸矩阵），并闭锁桥备自投
- 两级备自投：110kV 桥备自投 1.5s、10kV 分段备自投 3s，**时间级差配合**——进线失电时上级先动作恢复电压，下级不动作；正常方式持续 10s 才充电，手动分合相关开关立即放电
- 10kV 小电流接地：单相接地不跳闸，母线 3U0 升高、接地告警，遥控逐条拉开出线定位故障（拉路选线）
- 故障注入面板：瞬时/永久故障、单相接地、进线失电、主变故障、电压偏高、通道中断、消除故障、恢复正常方式；经独立的 Redis 仿真控制通道下发，不经过 104 数据通道
- 告警分类：保护动作后的分闸为事故跳闸（含主变保护跨间隔跳闸）、重合闸合闸、备自投分合闸

**事故追忆 PDR（`services/api/app/routers/pdr.py`、`web/src/lib/replay.ts`）**
- 事故总 / 保护 / 自动装置动作自动生成追忆，同一次事故 20s 内的后续动作合并
- 回放数据 = 触发前 30s 的全站断面 + 窗口内全部遥测变化与 SOE；前端按设备时标逐条重放，接线图重现开关动作与拓扑着色
- 0.1×~5× 播放、逐事件单步、SOE 列表与关键量曲线点击跳转

**运行报表（`services/api/app/reports`）**
- 负荷特性（最大/最小/平均负荷、负荷率、峰谷差率）、负荷曲线
- 电压合格率按 1 分钟平均电压统计，停电时间不计入；主变最大负载率、最高油温、分接开关动作次数；出线最大电流、事故跳闸次数；电量取电度表读数差
- 历史库只存变化：统计前按"前值保持"补齐没有变化记录的分钟，避免值不变的时段被漏算
- Excel 导出（openpyxl）：概况、负荷曲线（含折线图）、电压合格率、主变、出线五个工作表

**智能分析（P6）**
- 遥测数据校核（`app/alarm/datacheck.py`）：利用量测冗余发现坏数据
  - 节点功率平衡：10kV 各段母线（分段合位时合并）、110kV 内桥节点、主变高低压侧
  - 间隔自洽：√(P²+Q²) 与 √3·U·I 比对；负荷线路有功为负判为 CT 极性接反
  - 容差 = 各测点上送死区之和 + 相对误差（主站断面里的值最多偏离一个死区）；停运、小电流、品质无效的量测不参与
  - 异常持续 15 秒才告警，消失 15 秒才恢复，避免短路暂态误报；功率不平衡结合间隔自洽定位可疑间隔
  - 故障注入面板的"量测故障"（CT 极性接反 / 变送器卡死 / 电流量程错误）用于演示：一次设备正常，只有测量回路出错
- 短期负荷预测（`app/forecast`）：相似日法
  - 前 7 个仿真日同一时刻加权平均（近日权重大），剔除偏离中位数 25% 以上的值（事故停电、转供）
  - 用最近 1 小时实测与基准的比值做滚动修正，随预测步长指数衰减，限幅 ±15%
  - 上一仿真日日前预测回测，准确率按调度考核口径 1 − √(mean(((实际 − 预测) / 实际)²))；用子站同样的负荷曲线离线回测约 99%
  - 仿真负荷没有气象、节假日等外部特征，LightGBM 等模型在这里只能学到"同时刻历史值"，所以选了可解释的相似日法；接口按"历史序列 → 预测序列"设计，接入真实数据后可替换
  - 仿真时钟固定对齐本地时间（60 倍速时每个整点、24 分、48 分为仿真零点，`scadalib/simclock.py`），子站重启后历史数据的仿真钟点不变
- 事故简报（`app/brief`）
  - 从事故追忆数据提取事实：动作序列（设备时标）、开关变位、失电出线与损失负荷、故障最大电流、母线最低电压；事故过程以最后一个保护或自动装置信号后 2 秒为界，之后的操作单列
  - 规则定性（主变内部故障 / 永久性故障 / 瞬时性故障 / 电源侧失电 / 单相接地）并给出处置建议，生成规则模板简报
  - 配置 `ANTHROPIC_API_KEY` 后可由大模型（默认 `claude-opus-5-5`，`BRIEF_MODEL` 可改）基于同一份事实重写；模型只拿到提取的事实，不接触原始数据。请求开启了服务端 `fallbacks: "default"`：模型拒答时由服务端换用其他模型重试

**实时与历史**
- 前置：遥测死区在 RTU 侧、变化检测在前置侧；通道中断全站品质置 NT，恢复后总召刷新
- 历史：Redis Stream 消费组 + 批量 COPY，写库成功才 ACK（至少一次），重启先处理未确认消息
- TimescaleDB hypertable、1 分钟连续聚合、3 天后压缩、30 天保留；趋势查询按跨度自动选原始/聚合
- 推送：按画面订阅、200ms 合帧、只推增量；SOE 按设备时标排序

## 本地开发（WSL）

```bash
make venv       # 创建 .venv（全部 Python 服务 + 测试工具）
make dev        # 后端全链路 db/redis/rtu-sim/fep/api → http://localhost:18000/docs
make web-dev    # 前端 Vite → http://localhost:5173
make up-full    # 生产形态（含网关）→ http://localhost:18088
make test       # 规约栈/仿真/前置/api 的 pytest + 前端 vitest
make lint       # ruff + vue-tsc
make points     # 导出点表 docs/点表.csv
```

本机端口（WSL mirrored 网络下 Windows 占用了 8000/8080/18080）：api `18000`、网关 `18088`、仿真子站 104 `12404`（可用第三方 104 调试工具连接观察）。均可在 `.env` 修改。

## 部署到 VPS

1. VPS 安装 Docker，创建 `/opt/ai-scada/.env`（参考 `.env.example`）：
   - `SITE_ADDRESS=你的域名`（DNS 指向 VPS，Caddy 自动签发 HTTPS 证书）
   - `IMAGE_PREFIX=ghcr.io/<github用户名小写>/<仓库名小写>`
   - `POSTGRES_PASSWORD=<强密码>`
   - `JWT_SECRET=<openssl rand -hex 32>`；公开演示保留 `DEMO_MODE=true`（登录框展示演示账号），否则设为 `false` 并修改口令
2. GitHub 仓库设置：Variables `DEPLOY_ENABLED=true`；Secrets `VPS_HOST` / `VPS_USER` / `VPS_SSH_KEY`；GHCR 包设为 public（或在 VPS 上 `docker login ghcr.io`）。
3. 推送到 `main`：CI 测试 → 构建 api / fep / rtu-sim / gateway 镜像 → 上传 compose 文件 → `docker compose pull && up -d`。

VPS 只开放 80/443；数据库、Redis 和 104 端口仅在 compose 内网可达。

## 接口

- `GET /api/health` 就绪探针（数据库、Redis、后台任务状态）· `GET /api/health/live` 存活探针
- `GET /api/model/station` · `GET /api/model/points?cat=&bay=` 厂站模型与点表
- `GET /api/realtime?ids=` 实时快照 · `GET /api/history/{点号}?start=&end=` 历史曲线
- `GET /api/events/yx` SOE · `GET /api/channels` 通道状态 · `GET /api/channels/{通道}/frames?after=` 报文
- `WS /ws/realtime` 订阅推送（协议见 `services/api/app/routers/realtime.py`），另推送告警 / 确认 / 挂牌 / 遥控进度
- `POST /api/auth/login` 登录 · `GET /api/auth/me`
- `GET /api/alarms` 告警查询 · `GET /api/alarms/active` · `POST /api/alarms/ack` 确认
- `GET /api/control/check` 五防预校验 · `POST /api/control/sessions` 发起遥控，`/{id}/supervise|select|execute|cancel` · `POST /api/control/adjust` 遥调
- `GET|POST /api/tags` · `DELETE /api/tags/{id}` 挂牌 · `GET /api/audit` 审计（监护员/管理员）
- `GET /api/sim` 场景与自动装置状态 · `POST /api/sim/inject` 注入场景（需登录）
- `GET /api/pdr` 事故追忆列表 · `GET /api/pdr/{id}` 回放数据
- `GET /api/reports/summary?start=&end=` 运行报表 · `GET /api/reports/export.xlsx` 导出 Excel
- `GET /api/datacheck` 数据校核断面 · `GET /api/forecast/load` 负荷预测与回测
- `GET /api/pdr/{id}/brief` 事故简报 · `POST /api/pdr/{id}/brief` 重新生成（`{"llm": true}` 由大模型撰写）
