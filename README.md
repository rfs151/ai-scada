# PowerGrid SCADA Demo

电力系统 SCADA 演示系统：仿真 110kV 变电站经 **IEC 60870-5-104** 规约接入，主站完成采集、实时处理、告警/SOE、遥控（选择-返校-执行 + 五防）、一次接线图拓扑着色与事故追忆。

> 完整设计见 [docs/开发方案.md](docs/开发方案.md)。当前进度：**P0 骨架** ✅

## 架构

```
浏览器 ──HTTPS/WSS──► gateway (Caddy + Vue3 静态)
                         │ /api /ws
                         ▼
                        api (FastAPI) ──► TimescaleDB (历史/模型/事件)
                         ▲
                         └── Redis (实时库/事件总线) ◄── fep (IEC104 主站, P1) ◄── rtu-sim (仿真子站, P1)
```

| 目录 | 说明 |
|---|---|
| `services/api` | SCADA 服务：REST、WebSocket、健康检查 |
| `web` | Vue 3 + TS + Naive UI 人机界面；`web/Dockerfile` 构建网关镜像 |
| `deploy` | Caddyfile、数据库初始化脚本 |
| `.github/workflows/ci.yml` | 测试 → 构建镜像推送 GHCR → SSH 部署 VPS |

## 本地开发（WSL）

```bash
make dev        # db + redis + api(热重载) → http://localhost:18000/docs
make web-dev    # 前端 Vite → http://localhost:5173（代理 /api /ws 到 18000）
make up-full    # 生产形态（含网关）→ http://localhost:18088
make test       # pytest + vitest
make lint       # ruff + vue-tsc
make help       # 全部命令
```

本机端口：WSL mirrored 网络下 Windows 占用了 8000/8080/18080，因此开发端口为 api `18000`、网关 `18088`，可通过 `.env` 的 `API_DEV_PORT` / `GATEWAY_DEV_PORT` 修改。

国内构建加速：`.env` 中设置 `PIP_INDEX_URL`、`NPM_REGISTRY`（仅影响镜像构建）。

## 部署到 VPS

1. VPS 安装 Docker，创建 `/opt/ai-scada/.env`（参考 `.env.example`）：
   - `SITE_ADDRESS=你的域名`（DNS 指向 VPS，Caddy 自动签发 HTTPS 证书）
   - `IMAGE_PREFIX=ghcr.io/<github用户名小写>/<仓库名小写>`
   - `POSTGRES_PASSWORD=<强密码>`
2. GitHub 仓库设置：Variables `DEPLOY_ENABLED=true`；Secrets `VPS_HOST` / `VPS_USER` / `VPS_SSH_KEY`；GHCR 包设为 public（或在 VPS 上 `docker login ghcr.io`）。
3. 推送到 `main`：CI 测试 → 构建 `api`、`gateway` 镜像 → 上传 compose 文件 → `docker compose pull && up -d`。

VPS 只开放 80/443，数据库、Redis 和规约端口仅在 compose 内网可达。

## 健康检查

- `GET /api/health/live` 存活探针（容器 healthcheck 用）
- `GET /api/health` 就绪探针：并行检查 TimescaleDB 与 Redis，各自 2s 超时；任一异常返回 503 + 详细原因
- `WS /ws/realtime` 心跳 + ping/pong；前端指数退避重连、假死检测、服务器时钟偏差修正
