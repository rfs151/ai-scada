SHELL := /bin/bash
DEV  := docker compose -f docker-compose.yml -f docker-compose.dev.yml
API  := services/api
# 命令行下载默认直连，不继承本机代理变量
NOPROXY := env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY

.PHONY: help env dev web-dev up-full down logs ps test test-api test-web lint fmt build clean

help:  ## 列出命令
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

env:   ## 生成本地 .env
	@test -f .env || (cp .env.example .env && sed -i -e 's/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=scada-dev/' -e 's/^IMAGE_PREFIX=.*/IMAGE_PREFIX=ai-scada/' -e 's/^IMAGE_TAG=.*/IMAGE_TAG=dev/' .env && echo ".env created")

dev: env  ## 启动 db/redis/api（api 热重载，端口 18000）
	$(DEV) up -d --build db redis api

web-dev:  ## 本机启动前端 Vite（http://localhost:5173）
	cd web && npm run dev -- --host

up-full: env  ## 启动全部服务含网关（http://localhost:18088，生产形态）
	$(DEV) --profile full up -d --build

down:  ## 停止
	$(DEV) --profile full down

logs:  ## 跟踪日志
	$(DEV) logs -f --tail=100

ps:
	$(DEV) --profile full ps

$(API)/.venv:
	python3 -m venv $(API)/.venv
	$(NOPROXY) $(API)/.venv/bin/pip install -q -r $(API)/requirements-dev.txt

test: test-api test-web  ## 运行全部测试

test-api: $(API)/.venv
	cd $(API) && .venv/bin/pytest -q

test-web:
	cd web && npm test

lint: $(API)/.venv  ## 代码检查
	cd $(API) && .venv/bin/ruff check . && .venv/bin/ruff format --check .
	cd web && npm run typecheck

fmt: $(API)/.venv
	cd $(API) && .venv/bin/ruff check --fix . && .venv/bin/ruff format .

build:  ## 构建生产镜像
	docker compose build

clean:  ## 停止并删除数据卷（清空数据库）
	$(DEV) --profile full down -v
