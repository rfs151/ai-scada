#!/usr/bin/env bash
# 本机开发模式：db/redis 跑在 Docker，rtu-sim/fep/api/web 直接跑在本机（tmux 会话里各占一个窗口）。
#   scripts/local.sh up      启动并自检
#   scripts/local.sh down    停止本机进程（db/redis 容器保留）
#   scripts/local.sh check   自检：API 就绪、104 通道已连、数据在刷新、前端可访问
#   scripts/local.sh restart <服务>  重启单个进程（rtu-sim|fep|api|web）
#   scripts/local.sh attach  进入 tmux 会话看日志（Ctrl-b n 切换窗口，Ctrl-b d 退出）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SESSION=ai-scada-local
RUN="$ROOT/.run"
API_PORT="${API_DEV_PORT:-18000}"
WEB_PORT=5173
DC=(docker compose -f "$ROOT/docker-compose.yml" -f "$ROOT/docker-compose.dev.yml" --profile full)
CURL=(curl -fsS --noproxy '*' --max-time 3)
WINDOWS=(rtu-sim fep api web)

say() { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()  { printf '  \033[32m✔\033[0m %s\n' "$*"; }
bad() { printf '  \033[31m✘\033[0m %s\n' "$*"; }

# 新建一个 tmux 窗口；进程退出后窗口保留（remain-on-exit 是窗口级选项，每个窗口都要设）
start_window() {
  local name=$1 cmd=$2
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux new-window -d -t "$SESSION" -n "$name" "$cmd"
  else
    tmux new-session -d -s "$SESSION" -n "$name" "$cmd"
  fi
  tmux set-option -w -t "$SESSION:$name" remain-on-exit on
}

port_busy() { ss -ltn "sport = :$1" | grep -q LISTEN; }

# 每个服务的启动命令：先加载 .env 和 venv，输出同时写入 .run/<名字>.log
service_cmd() {
  local name=$1 dir=$2; shift 2
  printf 'cd %q && set -a && . %q && set +a && . %q && cd %q && %s 2>&1 | tee %q' \
    "$ROOT" "$ROOT/.env" "$ROOT/.venv/bin/activate" "$dir" "$*" "$RUN/$name.log"
}

up() {
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    say "已在运行（$SESSION），只做自检"; check; return
  fi
  [[ -f "$ROOT/.env" ]] || make -C "$ROOT" env
  make -C "$ROOT" -s venv
  [[ -d "$ROOT/web/node_modules" ]] || (cd "$ROOT/web" && npm ci)

  say "停止 Docker 里的业务容器（避免两套 fep/仿真同时写 Redis）"
  "${DC[@]}" stop gateway api fep rtu-sim >/dev/null 2>&1 || true

  say "启动 db、redis 容器并等待健康"
  "${DC[@]}" up -d --wait db redis

  for p in 2404 "$API_PORT" "$WEB_PORT"; do
    if port_busy "$p"; then bad "端口 $p 已被占用：$(ss -ltnp "sport = :$p" | tail -n +2)"; exit 1; fi
  done

  mkdir -p "$RUN"
  # shellcheck disable=SC1091
  set -a; . "$ROOT/.env"; set +a
  local db_url="postgresql://${POSTGRES_USER:-scada}:${POSTGRES_PASSWORD}@localhost:5432/${POSTGRES_DB:-scada}"
  local common="TZ=Asia/Shanghai REDIS_URL=redis://localhost:6379/0 SIM_SPEED=${SIM_SPEED:-60}"

  say "在 tmux 会话 $SESSION 中启动 rtu-sim / fep / api / web"
  start_window rtu-sim "$(service_cmd rtu-sim services/rtu-sim "$common python -m rtusim")"
  sleep 1   # 先让子站开始监听，fep 第一次连接就能成功
  start_window fep "$(service_cmd fep services/fep "$common RTU_HOST=127.0.0.1 RTU_PORT=2404 GI_PERIOD_S=300 python -m fep")"
  start_window api "$(service_cmd api services/api "$common DATABASE_URL='$db_url' APP_VERSION=local LOG_LEVEL=debug uvicorn app.main:app --reload --host 127.0.0.1 --port $API_PORT")"
  start_window web "$(service_cmd web web "npm run dev -- --host 127.0.0.1 --port $WEB_PORT --strictPort")"
  say "等待服务就绪"
  local i
  for i in $(seq 1 60); do
    "${CURL[@]}" "http://127.0.0.1:$API_PORT/api/health" >/dev/null 2>&1 \
      && "${CURL[@]}" "http://127.0.0.1:$WEB_PORT/" >/dev/null 2>&1 && break
    sleep 1
  done
  sleep 3   # 给 fep 留时间完成总召唤
  check
}

check() {
  local fail=0 h ch n1 n2
  say "自检"
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    local w state problems=""
    for w in "${WINDOWS[@]}"; do
      state=$(tmux display-message -p -t "$SESSION:$w" '#{pane_dead}' 2>/dev/null || echo missing)
      [[ "$state" == 0 ]] || problems+=" $w"
    done
    if [[ -z "$problems" ]]; then ok "tmux 会话 $SESSION：${#WINDOWS[@]} 个进程都在运行"
    else bad "已退出的进程:$problems（看 .run/<名字>.log，重启用 make local-restart S=<名字>）"; fail=1; fi
  else
    bad "tmux 会话 $SESSION 不存在（先执行 make local）"; return 1
  fi

  if h=$("${CURL[@]}" "http://127.0.0.1:$API_PORT/api/health" 2>/dev/null); then
    ok "API 就绪：$h" | cut -c1-160
  else bad "API 未就绪：http://127.0.0.1:$API_PORT/api/health"; fail=1; fi

  ch=$("${DC[@]}" exec -T redis redis-cli hget ch:rtu1 state 2>/dev/null | tr -d '\r')
  if [[ "$ch" == up ]]; then ok "104 通道 rtu1：up"
  else bad "104 通道 rtu1：${ch:-无状态}"; fail=1; fi

  n1=$("${DC[@]}" exec -T redis redis-cli xinfo stream rt:chg 2>/dev/null | awk 'p{print;exit} /last-generated-id/{p=1}' | tr -d '\r')
  sleep 3
  n2=$("${DC[@]}" exec -T redis redis-cli xinfo stream rt:chg 2>/dev/null | awk 'p{print;exit} /last-generated-id/{p=1}' | tr -d '\r')
  if [[ -n "$n2" && "$n1" != "$n2" ]]; then ok "实时数据在刷新（rt:chg $n1 → $n2）"
  else bad "实时数据 3 秒内没有变化（rt:chg ${n2:-空}）"; fail=1; fi

  if "${CURL[@]}" "http://127.0.0.1:$WEB_PORT/api/health/live" >/dev/null 2>&1; then
    ok "前端 http://localhost:$WEB_PORT 可访问，/api 代理正常"
  else bad "前端或 /api 代理不通：http://127.0.0.1:$WEB_PORT"; fail=1; fi

  if ((fail)); then bad "自检未通过"; return 1; fi
  say "全部正常 → 浏览器打开 http://localhost:$WEB_PORT（日志：make local-attach 或 .run/*.log）"
}

down() {
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    # 先发 Ctrl-C 让各进程正常退出，再关掉会话
    local w
    for w in web api fep rtu-sim; do tmux send-keys -t "$SESSION:$w" C-c 2>/dev/null || true; done
    sleep 2
    tmux kill-session -t "$SESSION"
    say "已停止本机进程（db/redis 容器仍在运行；完全停止用 make down）"
  else
    say "没有在运行"
  fi
}

restart() {
  local name=${1:?用法: $0 restart <rtu-sim|fep|api|web>}
  tmux respawn-window -k -t "$SESSION:$name"   # 用原来的启动命令重新拉起
  say "已重启 $name"
}

case "${1:-up}" in
  up) up ;;
  down) down ;;
  check) check ;;
  restart) restart "${2:-}" ;;
  attach) exec tmux attach -t "$SESSION" ;;
  *) echo "用法: $0 {up|down|check|restart <服务>|attach}"; exit 2 ;;
esac
