# 构建上下文为仓库根目录（需要共享库 packages/scadalib 与厂站模型 config/）
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STATION_CONFIG=/config/station.yaml

# 国内构建可传入镜像源，例如 https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_INDEX_URL=""

WORKDIR /app
COPY services/fep/requirements.txt /tmp/requirements.txt
RUN if [ -n "$PIP_INDEX_URL" ]; then pip config set global.index-url "$PIP_INDEX_URL"; fi \
    && pip install -r /tmp/requirements.txt

COPY packages/scadalib /tmp/scadalib
RUN pip install --no-deps /tmp/scadalib && rm -rf /tmp/scadalib

COPY config /config
COPY services/fep/fep ./fep

RUN useradd --system --uid 10001 scada
USER scada

CMD ["python", "-m", "fep"]
