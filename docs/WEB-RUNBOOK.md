# 前端网页启动指南

## 方式一：最快看到页面（仅前端，无数据）

适合：只想看 UI 效果，不依赖后端 API。

```bash
cd /Users/jingbozuo/Desktop/aln-data-master/frontend
npm run dev
```

浏览器打开：
```
http://localhost:5173
```

**效果**：页面能加载，但所有调用 `/api/xxx` 的地方会报 502（因为后端没起）。

停止：按 `Ctrl + C`。

---

## 方式二：开发模式（前端 + 后端 + 数据库）

需要 **3 个终端窗口**同时运行。

### 终端 1：启动数据库

```bash
cd /Users/jingbozuo/Desktop/aln-data-master/deploy

# 先修正 .env 中的 DATA_ROOT（当前指向 Downloads，需要改成 Desktop）
# 或者创建软链接:
mkdir -p /Users/jingbozuo/Downloads/aln-data-master
cp -r /Users/jingbozuo/Desktop/aln-data-master/data /Users/jingbozuo/Downloads/aln-data-master/

# 启动 postgres + redis
docker compose up postgres redis
```

### 终端 2：启动后端 API

```bash
cd /Users/jingbozuo/Desktop/aln-data-master/backend

# 安装缺失依赖
pip install scikit-rf sqlalchemy psycopg fastapi uvicorn

# 启动 API
uvicorn app.main:app --reload --port 8000
```

验证后端是否就绪：
```bash
curl http://localhost:8000/api/health
```

### 终端 3：启动前端

```bash
cd /Users/jingbozuo/Desktop/aln-data-master/frontend
npm run dev
```

浏览器打开：
```
http://localhost:5173
```

**原理**：
- 前端 `vite dev server` 运行在 **5173** 端口
- Vite 配置里把 `/api/*` 代理到 `http://localhost:8000`
- 后端 FastAPI 运行在 **8000** 端口
- 前端页面调用 `/api/devices/...` 时，Vite 自动转发给后端

---

## 方式三：Docker Compose 全栈启动（推荐用于演示）

适合：一键启动完整系统（前端 + 后端 + 数据库 + nginx）。

### 前置步骤

当前 `.env` 里的 `DATA_ROOT` 指向 `Downloads`，但项目在 `Desktop`，需要统一：

```bash
cd /Users/jingbozuo/Desktop/aln-data-master

# 方案 A：修改 .env（推荐）
sed -i '' 's|/Users/jingbozuo/Downloads/aln-data-master/data|/Users/jingbozuo/Desktop/aln-data-master/data|' .env

# 方案 B：创建软链接
mkdir -p /Users/jingbozuo/Downloads/aln-data-master
cp -r data /Users/jingbozuo/Downloads/aln-data-master/
```

### 启动

```bash
cd /Users/jingbozuo/Desktop/aln-data-master/deploy
docker compose up --build
```

等待所有容器 `healthy` 后，浏览器打开：
```
http://localhost
```

nginx 会把 `/api/*` 转发给后端，静态文件走前端 dist/。

停止：
```bash
cd deploy && docker compose down
```

---

## 端口速查

| 服务 | 端口 | 访问地址 |
|---|---|---|
| 前端开发服务器 | 5173 | http://localhost:5173 |
| 后端 API | 8000 | http://localhost:8000/docs （Swagger UI）|
| nginx（Docker） | 80 | http://localhost |
| PostgreSQL | 15432 | localhost:15432 |
| Redis | 6379 | localhost:6379 |

---

## 常见问题

**Q: `npm run dev` 报错 `Cannot find module 'vite'`？**  
A: `cd frontend && npm install`

**Q: 后端启动报错 `ModuleNotFoundError: No module named 'skrf'`？**  
A: `pip install scikit-rf sqlalchemy psycopg fastapi uvicorn`

**Q: Docker 启动报错 `need DATA_ROOT in .env`？**  
A: `.env` 里的 `DATA_ROOT` 必须是绝对路径，且目录存在。按上方"前置步骤"修改。

**Q: 浏览器显示 `502 Bad Gateway`？**  
A: 后端没起或端口不对。检查 `curl http://localhost:8000/api/health` 是否返回 `{"status":"ok"}`。

**Q: 前端页面空白，控制台报 CORS 错误？**  
A: 确保走 `http://localhost:5173`（Vite 代理），而不是直接访问 `http://localhost:8000`。
