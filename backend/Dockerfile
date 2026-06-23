# 谐振器数据平台 - 后端镜像（FastAPI + Celery 共用）
# 全部依赖走预编译 wheel：psycopg[binary] / numpy / scipy / scikit-rf，无需 build-essential / libpq-dev。
FROM python:3.12-slim

# uv 包管理器
COPY --from=ghcr.io/astral-sh/uv:0.10 /uv /usr/local/bin/uv

WORKDIR /app

# 先装依赖（利用层缓存）
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev || uv sync --no-dev

# 拷代码
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
