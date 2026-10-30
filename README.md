# PowerGrid SCADA Demo

电力系统 SCADA 演示系统：仿真 110kV 变电站经 **IEC 60870-5-104** 规约接入，主站完成采集、实时处理、告警/SOE、遥控（选择-返校-执行 + 五防）、一次接线图拓扑着色与事故追忆。

> 完整设计见 [docs/开发方案.md](docs/开发方案.md)，点表见 [docs/点表.csv](docs/点表.csv)。
> 进度：**P0 骨架** ✅ · **P1 数据链路** ✅ · P2 接线图 · P3 告警/遥控/五防 · P4 故障场景

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
2. GitHub 仓库设置：Variables `DEPLOY_ENABLED=true`；Secrets `VPS_HOST` / `VPS_USER` / `VPS_SSH_KEY`；GHCR 包设为 public（或在 VPS 上 `docker login ghcr.io`）。
3. 推送到 `main`：CI 测试 → 构建 api / fep / rtu-sim / gateway 镜像 → 上传 compose 文件 → `docker compose pull && up -d`。

VPS 只开放 80/443；数据库、Redis 和 104 端口仅在 compose 内网可达。

## 接口

- `GET /api/health` 就绪探针（数据库、Redis、后台任务状态）· `GET /api/health/live` 存活探针
- `GET /api/model/station` · `GET /api/model/points?cat=&bay=` 厂站模型与点表
- `GET /api/realtime?ids=` 实时快照 · `GET /api/history/{点号}?start=&end=` 历史曲线
- `GET /api/events/yx` SOE · `GET /api/channels` 通道状态 · `GET /api/channels/{通道}/frames?after=` 报文
- `WS /ws/realtime` 订阅推送（协议见 `services/api/app/routers/realtime.py`）
