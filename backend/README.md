# aln-backend

谐振器测试数据平台 - FastAPI + Celery 后端

## 本地开发

```bash
# 1. 装依赖
cd backend
uv sync

# 2. 启动数据服务（容器）
cd ../deploy
podman compose --env-file ../.env up -d postgres redis

# 3. 跑迁移
cd ../backend
uv run alembic upgrade head

# 4. 启动 API（reload 模式）
uv run uvicorn app.main:app --reload --port 8000

# 5. 另一个终端：启动 worker
uv run celery -A app.workers worker --loglevel=info --concurrency=4
```

## 跑测试

```bash
uv run pytest
uv run pytest tests/core -v          # 仅算法层
uv run pytest --cov=app --cov-report=html
```

## 目录

```
backend/
├── app/
│   ├── api/         FastAPI 路由
│   ├── core/        算法层（纯函数）
│   ├── models/      SQLAlchemy ORM
│   ├── schemas/     Pydantic 请求/响应模型
│   ├── workers/     Celery 任务
│   ├── config.py    Settings + AlgorithmConfig
│   └── main.py      入口
├── alembic/         数据库迁移
└── tests/           pytest
    ├── core/        算法层单元测试
    ├── api/         API 集成测试
    └── fixtures/    样例数据（建议软链 ../../客户提供的材料/）
```

## 文档

- 架构：`../docs/architecture.md`
- 算法移植规格：`../docs/algorithm-port.md`
- 数据库 schema：`../docs/database-schema.md`
- API 契约：`../docs/api.md`
- 部署：`../docs/deployment.md`
