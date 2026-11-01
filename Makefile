SHELL := /bin/bash
DEV  := docker compose -f docker-compose.yml -f docker-compose.dev.yml
VENV := .venv
PY   := $(VENV)/bin
# 命令行下载默认直连，不继承本机代理变量
NOPROXY := env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY
PY_SUITES := packages/scadalib services/rtu-sim services/fep services/api

.PHONY: help env venv dev web-dev local local-stop local-check local-restart local-attach up-full down logs ps test test-py test-web lint fmt build clean points

help:  ## 列出命令
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[36m%-13s\033[0m %s\n", $$1, $$2}'

env:   ## 生成本地 .env
	@test -f .env || (cp .env.example .env && sed -i -e 's/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=scada-dev/' -e 's/^IMAGE_PREFIX=.*/IMAGE_PREFIX=ai-scada/' -e 's/^IMAGE_TAG=.*/IMAGE_TAG=dev/' .env && echo ".env created")

$(VENV)/.installed: requirements-dev.txt services/*/requirements.txt
	test -d $(VENV) || python3 -m venv $(VENV)
	$(NOPROXY) $(PY)/pip install -q -r requirements-dev.txt
	touch $@

venv: $(VENV)/.installed  ## 创建本地 Python 开发环境

dev: env  ## 启动后端全链路：db/redis/rtu-sim/fep/api（api 热重载，端口 18000）
	$(DEV) up -d --build db redis rtu-sim fep api

web-dev:  ## 本机启动前端 Vite（http://localhost:5173）
	cd web && npm run dev -- --host

local:  ## 本机开发模式一键启动并自检：db/redis 用 Docker，其余跑在本机 tmux（http://localhost:5173）
	@scripts/local.sh up

local-stop:  ## 停止本机开发模式进程（db/redis 容器保留）
	@scripts/local.sh down

local-check:  ## 本机开发模式自检
	@scripts/local.sh check

local-restart:  ## 重启本机开发模式的单个进程：make local-restart S=fep
	@scripts/local.sh restart $(S)

local-attach:  ## 进入本机开发模式的 tmux 会话看日志
	@scripts/local.sh attach

up-full: env  ## 启动全部服务含网关（http://localhost:18088，生产形态）
	$(DEV) --profile full up -d --build

down:  ## 停止
	$(DEV) --profile full down

logs:  ## 跟踪日志
	$(DEV) logs -f --tail=100

ps:
	$(DEV) --profile full ps

test: test-py test-web  ## 运行全部测试

test-py: venv
	@set -e; for d in $(PY_SUITES); do echo "== $$d"; (cd $$d && ../../$(PY)/pytest -q); done

test-web:
	cd web && npm test

lint: venv  ## 代码检查
	$(PY)/ruff check packages services && $(PY)/ruff format --check packages services
	cd web && npm run typecheck

fmt: venv
	$(PY)/ruff check --fix packages services && $(PY)/ruff format packages services

points: venv  ## 导出点表 docs/点表.csv
	$(PY)/python -c "from scadalib.model import *; print(load_station(default_config_path()).point_table_csv(), end='')" > docs/点表.csv
	@echo "docs/点表.csv: $$(($$(wc -l < docs/点表.csv) - 1)) points"

build:  ## 构建生产镜像
	docker compose build

clean:  ## 停止并删除数据卷（清空数据库）
	$(DEV) --profile full down -v
