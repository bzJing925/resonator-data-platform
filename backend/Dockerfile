# 谐振器数据平台 - 后端镜像（FastAPI + Celery 共用）
# 全部依赖走预编译 wheel：psycopg[binary] / numpy / scipy / scikit-rf，无需 build-essential / libpq-dev。
FROM python:3.12-slim AS builder

# uv 包管理器
COPY --from=ghcr.io/astral-sh/uv:0.10 /uv /usr/local/bin/uv

WORKDIR /app

# zipfile-deflate64 是纯源码分发（uv.lock 只有 sdist），uv sync 时需要 C 编译器。
# 仅 builder 阶段安装，最终镜像不带编译工具链。
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖（利用层缓存）
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev || uv sync --no-dev

FROM python:3.12-slim

WORKDIR /app

# builder 与最终镜像同基座、同路径，.venv 内 shebang 指向 /usr/local/bin/python 可直接复用
COPY --from=builder /app/.venv ./.venv
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
