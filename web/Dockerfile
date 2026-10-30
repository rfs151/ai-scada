# 网关镜像：构建前端静态文件，打包进 Caddy。构建上下文为仓库根目录。
FROM node:22-alpine AS build
ARG NPM_REGISTRY=""
WORKDIR /src
COPY web/package.json web/package-lock.json ./
RUN if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi \
    && npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM caddy:2.9-alpine
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /src/dist /srv
