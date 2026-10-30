FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 国内构建可传入镜像源，例如 https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_INDEX_URL=""

WORKDIR /app
COPY requirements.txt .
RUN if [ -n "$PIP_INDEX_URL" ]; then pip config set global.index-url "$PIP_INDEX_URL"; fi \
    && pip install -r requirements.txt

COPY app ./app

RUN useradd --system --uid 10001 scada
USER scada

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
