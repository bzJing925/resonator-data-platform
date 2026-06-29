# 运维 Cookbook

**版本**：v0.1
**适用对象**：所内值班工程师、平台管理员
**前置阅读**：`deployment.md`（首次部署）、`architecture.md`（系统总览）

本手册聚焦**日常运维**和**故障处理**，不重复部署流程。所有命令均可直接复制执行；命令里的容器名、端口、路径均与 `deploy/docker-compose.yml` 一致。

> **路径约定**：命令中的 `$REPO` 指本仓库 clone 后的根目录，`$DATA_ROOT` 指 `.env` 中的 `DATA_ROOT`（默认 `/data3/aln`）。值班前请先 `export REPO=/your/path/to/aln-data`。

---

## 1. 服务状态速查

### 1.1 一行命令看全栈

```bash
cd $REPO/deploy && podman compose ps
```

正常输出（5 行 Up）：

```
NAME            IMAGE                            STATUS                   PORTS
aln-postgres    postgres:15-alpine               Up 3 hours (healthy)     127.0.0.1:15432->5432/tcp
aln-redis       redis:7-alpine                   Up 3 hours (healthy)     127.0.0.1:6379->6379/tcp
aln-api         localhost/deploy_api:latest      Up 3 hours               127.0.0.1:8000->8000/tcp
aln-worker      localhost/deploy_worker:latest   Up 3 hours
aln-nginx       nginx:alpine                     Up 3 hours (healthy)     0.0.0.0:8080->80/tcp
```

`STATUS` 关键词：

- `Up ... (healthy)`：容器跑着且 healthcheck 通过。
- `Up ... (unhealthy)`：进程在但 healthcheck 失败，看日志。
- `Up ... (starting)`：刚起来，30 秒后再看。
- `Restarting`：进程崩了被自动拉起，必须排查根因。
- `Exited (X)`：彻底死了，X 是退出码。

### 1.2 健康检查接口

```bash
curl -s http://localhost:8080/api/health | python3 -m json.tool
```

期望返回：

```json
{
  "status": "ok",
  "db": "ok",
  "redis": "ok",
  "disk_free_gb": 6432.5
}
```

任意子项不是 `ok`，立刻进对应章节排查（`db` → §5.5；`redis` → §5.6；`disk_free_gb` 低 → §5.7）。

### 1.3 各容器健康指标

| 容器 | 端口（host） | 健康检查命令 | 典型 CPU | 典型内存 |
|---|---|---|---|---|
| aln-postgres | 127.0.0.1:15432 | `podman exec aln-postgres pg_isready -U aln` | 1–5% | 200–500 MB |
| aln-redis | 127.0.0.1:6379 | `podman exec aln-redis redis-cli ping` | < 1% | 50–200 MB |
| aln-api | 127.0.0.1:8000 | `curl -s http://localhost:8000/api/health` | 5–15%（处理上传时高） | 300–800 MB |
| aln-worker | — | `podman exec aln-worker celery -A app.workers inspect ping` | 0%（idle） / 200%+（处理时） | 500 MB–2 GB |
| aln-nginx | 0.0.0.0:8080 | `curl -sI http://localhost:8080/` 返回 200 | < 1% | 20–50 MB |

资源实时查看：

```bash
podman stats --no-stream
```

---

## 2. 日常重启与升级

### 2.1 重启单个服务

```bash
cd $REPO/deploy
podman compose restart api      # 改 .env 后只重 api
podman compose restart worker   # 调 concurrency / 算法代码
podman compose restart nginx    # 改 nginx 配置 / 前端 dist
```

需要重启的常见情况：

- 改了 `.env`（API/worker 必须重启才能读到新环境变量）。
- 改了 `WORKER_CONCURRENCY`（worker 重启）。
- 改了 `PIPELINE_*` 配置项（worker 重启生效）。
- worker 死锁、长时间不消费队列。
- 修改了 `deploy/nginx/default.conf`（nginx 重启）。
- 前端 `npm run build` 输出新 dist（nginx 重启）。

数据**不会丢失**：所有持久化数据在 `/data3/aln/{pgdata,redis,uploads,files,...}`，volume 挂载，容器重启不影响。但**正在跑的上传任务会失败**，需要用户重新提交。

### 2.2 全栈停启

正确顺序：先停应用层，再停数据层；启动反过来。

```bash
cd $REPO/deploy

# 停（顺序：api → worker → nginx → redis → postgres）
podman compose stop nginx api worker
podman compose stop redis postgres

# 启（顺序：postgres → redis → api/worker → nginx）
podman compose up -d postgres redis
sleep 10  # 等 healthcheck
podman compose up -d api worker nginx
```

或者一把梭，依赖关系由 compose 处理（生产够用）：

```bash
podman compose down       # 删容器（保留数据）
podman compose up -d      # 全部起来
```

### 2.3 升级代码（最常见）

```bash
cd $REPO
git pull
cd deploy

# 仅后端代码改动
podman compose build api worker
podman compose up -d api worker

# 前端改动
cd $REPO/frontend
npm ci && npm run build
cd $REPO/deploy
podman compose restart nginx
```

如果伴随数据库 schema 变更（新 alembic revision）：

```bash
# 先停应用层（避免老代码读新表）
cd $REPO/deploy
podman compose stop api worker

# 跑迁移（容器内执行）
podman compose run --rm api alembic upgrade head

# 起回应用层
podman compose up -d api worker
```

容器外跑 alembic（开发机有 venv 时）：

```bash
cd $REPO/backend
DATABASE_URL=postgresql+psycopg://aln:$(grep POSTGRES_PASSWORD ../.env | cut -d= -f2)@127.0.0.1:15432/aln \
  uv run alembic upgrade head
```

### 2.4 升级 PostgreSQL / Redis 镜像

**小版本**（如 15.4 → 15.6，7.0 → 7.2）可直接换：

```bash
# 改 deploy/docker-compose.yml 的 image tag，然后
cd $REPO/deploy
podman compose pull postgres
podman compose up -d postgres
podman compose logs -f postgres   # 看启动日志
```

**大版本**（PG 15 → 16）**禁止**直接换 tag，pgdata 不兼容。须停机走 `pg_upgrade` 或 `pg_dump` + restore：

```bash
# 大致流程（实际执行前先备份）
podman exec aln-postgres pg_dumpall -U aln > /data3/aln/backups/before_pg16.sql
podman compose stop api worker postgres
mv /data3/aln/pgdata /data3/aln/pgdata.pg15.bak
mkdir /data3/aln/pgdata
# 改 image: postgres:16-alpine
podman compose up -d postgres
sleep 15
cat /data3/aln/backups/before_pg16.sql | podman exec -i aln-postgres psql -U aln
podman compose up -d api worker
```

Redis 7.x 系列内升级直接换 tag 即可，AOF 文件向前兼容。

---

## 3. 备份与恢复

### 3.1 每日数据库备份（cron）

`crontab -e` 添加：

```
0 2 * * * podman exec aln-postgres pg_dump -U aln aln | gzip > /data3/aln/backups/aln_$(date +\%F).sql.gz 2>>/data3/aln/logs/backup.log
0 3 * * 0 find /data3/aln/backups -name 'aln_*.sql.gz' -mtime +30 -delete
```

含义：每日 02:00 全库 dump + gzip；每周日 03:00 清 30 天前的备份。

首次执行前建目录：

```bash
mkdir -p /data3/aln/backups /data3/aln/logs
```

手动触发一次：

```bash
podman exec aln-postgres pg_dump -U aln aln | gzip > /data3/aln/backups/aln_$(date +%F).sql.gz
ls -lh /data3/aln/backups/
```

50 万行规模下 dump 文件 ~80–120 MB，预计 30 秒内完成。

### 3.2 验证备份

每月跑一次"恢复演练"——把昨天的备份 restore 到一个临时数据库，验证能不能 SELECT：

```bash
LATEST=$(ls -t /data3/aln/backups/aln_*.sql.gz | head -1)

# 创建临时库
podman exec aln-postgres psql -U aln -c "CREATE DATABASE aln_restore_test;"

# 恢复
gunzip -c "$LATEST" | podman exec -i aln-postgres psql -U aln aln_restore_test

# 抽样验证
podman exec aln-postgres psql -U aln aln_restore_test -c \
  "SELECT COUNT(*) FROM batches; SELECT COUNT(*) FROM devices;"

# 清理
podman exec aln-postgres psql -U aln -c "DROP DATABASE aln_restore_test;"
```

### 3.3 紧急恢复：全库回滚

误操作（误删批次、迁移出错、被 truncate 等）：

```bash
cd $REPO/deploy

# 1) 立即停应用，防止脏写继续
podman compose stop api worker nginx

# 2) 改名保留现场（不 drop，方便事后取证）
podman exec aln-postgres psql -U aln postgres -c \
  "ALTER DATABASE aln RENAME TO aln_corrupt_$(date +%Y%m%d_%H%M);"

# 3) 重建空库
podman exec aln-postgres psql -U aln postgres -c "CREATE DATABASE aln OWNER aln;"

# 4) 选定备份恢复
gunzip -c /data3/aln/backups/aln_2026-05-08.sql.gz | \
  podman exec -i aln-postgres psql -U aln aln

# 5) 抽样校验
podman exec aln-postgres psql -U aln aln -c \
  "SELECT batch_no, device_count, uploaded_at FROM batches ORDER BY uploaded_at DESC LIMIT 5;"

# 6) 启服务
podman compose up -d api worker nginx
```

### 3.4 紧急恢复：单表/单批次回滚

若只想恢复 `devices` 一张表，从全量 dump 里抽出来：

```bash
gunzip -c /data3/aln/backups/aln_2026-05-08.sql.gz | \
  awk '/^COPY public.devices /,/^\\\.$/' > /tmp/devices_only.sql

# 在 PG 里清空目标表后导入（注意会丢失截至备份后的所有写入）
podman exec aln-postgres psql -U aln aln -c "TRUNCATE devices;"
cat /tmp/devices_only.sql | podman exec -i aln-postgres psql -U aln aln
```

更精细的"回滚某个批次"通常需要 pgdump 配 `--data-only --table` 或 PITR，v1 没启用 WAL 归档，**接受"以备份时刻为准"**。

### 3.5 用户上传文件的备份

`/data3/aln/files/` 体积大（解压后的 .s1p）但**可由用户重新上传重建**，重要性中等。建议每周 rsync 到 NAS：

```bash
# crontab：每周日凌晨 4:00
0 4 * * 0 rsync -av --delete /data3/aln/files/ /mnt/nas/aln-files/ >> /data3/aln/logs/rsync.log 2>&1
```

`/data3/aln/uploads/`（原始 zip）和 `/data3/aln/exports/`（导出临时文件）**不需要备份**，定期清理即可（§5.7）。

---

## 4. 监控与告警

### 4.1 关键指标和阈值

| 指标 | 当前值 | 警戒线 | 检查命令 |
|---|---|---|---|
| /data3 磁盘占用 | ~5% | > 80% | `df -h /data3` |
| pgdata 大小 | < 1 GB | > 100 GB（超过预期） | `du -sh /data3/aln/pgdata` |
| PG 连接数 | < 10 | > 50 | 见下面 SQL |
| Redis 内存 | < 50 MB | > 500 MB | `podman exec aln-redis redis-cli INFO memory` |
| Celery 队列堆积 | 0 | > 100 | `podman exec aln-redis redis-cli LLEN celery` |
| pending 任务数 | < 5 | > 20 | 见下面 SQL |
| 5 分钟内 failed 任务 | 0 | > 3 | 见下面 SQL |

PG 连接数：

```sql
SELECT COUNT(*) FROM pg_stat_activity WHERE datname='aln';
```

任务堆积：

```sql
SELECT status, COUNT(*) FROM upload_tasks
WHERE started_at > now() - INTERVAL '1 hour'
GROUP BY status;
```

### 4.2 简单告警脚本

`$REPO/scripts/health_alert.sh`：

```bash
#!/usr/bin/env bash
# 每 6 小时跑一次，超阈值发邮件
set -uo pipefail

ALERT_TO="admin@example.com"
LOG=/data3/aln/logs/alert.log
TS=$(date '+%F %T')
ALERTS=()

# 1. 磁盘
USAGE=$(df /data3 | awk 'NR==2 {gsub("%",""); print $5}')
if [ "$USAGE" -gt 80 ]; then
  ALERTS+=("[DISK] /data3 used ${USAGE}%")
fi

# 2. 容器
for c in aln-postgres aln-redis aln-api aln-worker aln-nginx; do
  STATUS=$(podman inspect -f '{{.State.Status}}' "$c" 2>/dev/null || echo missing)
  if [ "$STATUS" != "running" ]; then
    ALERTS+=("[CONTAINER] $c is $STATUS")
  fi
done

# 3. 健康接口
HTTP=$(curl -sf -o /dev/null -w "%{http_code}" http://localhost:8080/api/health || echo 000)
if [ "$HTTP" != "200" ]; then
  ALERTS+=("[API] /api/health returned $HTTP")
fi

# 4. PG 连接数
CONN=$(podman exec aln-postgres psql -U aln -tAc \
  "SELECT COUNT(*) FROM pg_stat_activity WHERE datname='aln'" 2>/dev/null || echo 0)
if [ "$CONN" -gt 50 ]; then
  ALERTS+=("[PG] connection count ${CONN} > 50")
fi

# 5. Celery 队列
QLEN=$(podman exec aln-redis redis-cli LLEN celery 2>/dev/null || echo 0)
if [ "$QLEN" -gt 100 ]; then
  ALERTS+=("[CELERY] queue length ${QLEN}")
fi

if [ ${#ALERTS[@]} -gt 0 ]; then
  BODY=$(printf '%s\n' "${ALERTS[@]}")
  echo "$TS ALERT" >> "$LOG"
  printf '%s\n' "$BODY" >> "$LOG"
  printf '%s\n' "$BODY" | mail -s "[ALN] alert ($TS)" "$ALERT_TO"
else
  echo "$TS ok" >> "$LOG"
fi
```

启用：

```bash
chmod +x $REPO/scripts/health_alert.sh
crontab -e
# 加入：
# 0 */6 * * * $REPO/scripts/health_alert.sh
```

### 4.3 接入 Grafana / Prometheus（二期）

留个口子，二期再做。计划：

- `postgres_exporter` 抓 PG 指标。
- `redis_exporter` 抓 Redis。
- API 加 `/metrics` 端点（prometheus_fastapi_instrumentator）。
- Grafana dashboard 看任务量、查询耗时分布、错误率。

v1 不上，避免增加运维复杂度。

---

## 5. 排错手册

### 5.1 上传任务卡在 pending

症状：用户提交上传后，任务长时间停留在 `pending` 不进 `running`。

```bash
# 1. 看 worker 是否还活着
podman ps | grep aln-worker

# 2. 看 Celery 队列长度（堆积了任务但没人消费）
podman exec aln-redis redis-cli LLEN celery

# 3. 看 worker 日志是否有 traceback
podman logs aln-worker --tail 200

# 4. 看 worker 是否能 ping 通
podman exec aln-worker celery -A app.workers inspect ping

# 5. 修复：重启 worker
podman compose restart worker
```

如果重启后仍不消费，多半是 Redis 连不上（看 worker 日志中的 `kombu.exceptions.OperationalError`）；进入 §5.6。

### 5.2 上传任务标记 failed

先到 `upload_tasks` 表查具体错误：

```sql
SELECT id, batch_no, status, error_msg, started_at, finished_at
FROM upload_tasks
WHERE status='failed'
ORDER BY started_at DESC
LIMIT 10;
```

常见 `error_msg` 与处理：

| error_msg | 含义 | 处理 |
|---|---|---|
| `ZIP 解压后未发现 .s2p 文件` | 用户上传错文件 | 通知用户重传 |
| `重名批次号 ... 已存在` | 同批次号已上传 | 让用户改名或先删旧批次 |
| `mapping 解析失败：sheet 缺列 mark` | xlsx 格式不对 | 让用户用模板 |
| `disk full` / `No space left on device` | /data3 满 | §5.7 清盘 |
| `database connection refused` | PG 挂了 | §5.5 |
| `MemoryError` / OOM | worker 单任务太大 | 改小并发或分批 |
| `未安装 7z / unzip，无法流式解压` | worker 容器缺少解压工具 | 安装 `p7zip` 或 `unzip` 后重启 worker |
| `ZIP 解压后未发现可处理的 DUT 文件` | zip 内无有效 .s1p/.s2p | 检查上传包内容 |
| `无法为 ... 找到匹配的 OPEN/SHORT 校准件` | de-embed 开启但校准件缺失/不匹配 | 确认 zip 含同端口 OPEN/SHORT，或关闭 de-embed |

任务清理（已失败的占位记录想删掉）：

```sql
DELETE FROM upload_tasks WHERE status='failed' AND started_at < now() - INTERVAL '7 days';
```

### 5.3 SSE 进度推送不动

症状：上传成功，任务后台在跑，但前端进度条不动。

```bash
# 1. 浏览器 F12 → Network → 找 /api/tasks/<id>/stream
#    应该是 EventStream 类型，长连接保持
#    若直接 200 + 内容长度 = 0，多半是 nginx 在 buffering

# 2. 检查 nginx 配置中的 proxy_buffering
grep -n proxy_buffering $REPO/deploy/nginx/default.conf
# 期望看到 proxy_buffering off; 在 SSE 路由块里

# 3. 验证 worker 是否往 Redis publish
podman exec aln-redis redis-cli PSUBSCRIBE 'task:*'
# 然后另起一个终端发起新上传，订阅端应有事件刷出

# 4. 验证 API 是否在 subscribe
podman logs aln-api --tail 50 | grep -i "sse\|subscribe"
```

修复：

- nginx buffering 没关：编辑 `deploy/nginx/default.conf`，对 `/api/tasks/.+/stream` 加 `proxy_buffering off; proxy_cache off; proxy_read_timeout 24h;`，然后 `podman compose restart nginx`。
- worker 没 publish：通常是配置错误（`REDIS_URL` 不对），检查 worker 容器的环境变量 `podman exec aln-worker env | grep REDIS`。

### 5.4 查询慢

用户反馈"散点图卡"或"导出超时"。

```bash
# 进 PG 看活跃慢查询
podman exec -it aln-postgres psql -U aln aln
```

```sql
-- 长跑中的查询
SELECT pid, now()-query_start AS duration, state, query
FROM pg_stat_activity
WHERE state='active' AND query NOT LIKE '%pg_stat_activity%'
ORDER BY duration DESC;

-- EXPLAIN 关键查询
EXPLAIN (ANALYZE, BUFFERS)
SELECT fs_ghz, qs FROM devices
WHERE pf='Y' AND eg=0.5 AND fl=0.0
LIMIT 50000;
```

修复方向：

- 索引未走（`Seq Scan on devices`）：跑 `VACUUM ANALYZE devices;` 让规划器统计信息更新。
- 统计信息陈旧（行数估算偏差大于 10 倍）：同上 `ANALYZE`。
- 数据量爆炸（500 万行 + 多次 OR 条件）：让前端加更多筛选；考虑加物化视图。
- 杀掉一个失控查询：`SELECT pg_cancel_backend(<pid>);`，强杀用 `pg_terminate_backend(<pid>)`。

### 5.5 PostgreSQL 连不上

```bash
podman compose ps postgres
podman logs aln-postgres --tail 100
```

#### 端口被占

```
rootlessport listen tcp 127.0.0.1:15432: bind: address already in use
```

```bash
# 找占用进程
ss -tlnp | grep 15432

# 通常是上次 podman 没清干净
podman rm -f aln-postgres
podman compose up -d postgres
```

#### pgdata 权限问题

日志里出现 `could not change permissions of directory "/var/lib/postgresql/data/pgdata": Operation not permitted`：

```bash
sudo chown -R 999:999 /data3/aln/pgdata
podman compose restart postgres
```

#### 连接数打满

`FATAL: sorry, too many clients already`：

```sql
-- 看是谁建了一堆连接
SELECT application_name, state, COUNT(*)
FROM pg_stat_activity GROUP BY application_name, state;

-- 临时杀 idle 连接
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
WHERE state='idle' AND state_change < now() - INTERVAL '1 hour';
```

长期方案：API 端的 SQLAlchemy `pool_size` 调小或换 PgBouncer。

### 5.6 Redis 内存爆

```bash
podman exec aln-redis redis-cli INFO memory | grep -E 'used_memory_human|used_memory_peak_human|maxmemory_human'
podman exec aln-redis redis-cli DBSIZE
```

常见原因和处理：

- pub/sub 频道大量 publish 但没有订阅者堆积：v1 用 SSE，订阅者断开后 backlog 不应该累积，这种情况罕见；如真出现，重启 redis 即可（数据是 AOF 持久化，重启不会丢 Celery 任务）：

  ```bash
  podman compose restart redis
  ```

- Celery 大量 result 占内存：检查 `result_backend` 是否设了 expire（应在 worker 配置里设 `result_expires=3600`）。

- 客户端连接泄漏：`redis-cli CLIENT LIST` 看连接数。

### 5.7 磁盘满

```bash
df -h /data3
du -sh /data3/aln/* | sort -h
```

按这个顺序清理：

```bash
# 1) /data3/aln/uploads/ 中 7 天前的 zip（用户已经处理完，原 zip 没用了）
find /data3/aln/uploads -name '*.zip' -mtime +7 -delete

# 2) /data3/aln/exports/ 中 24 小时前的导出临时文件
find /data3/aln/exports -type f -mmin +1440 -delete

# 3) 老应用日志
find /data3/aln/logs -name '*.log' -mtime +14 -delete

# 4) 30 天前的数据库备份
find /data3/aln/backups -name 'aln_*.sql.gz' -mtime +30 -delete

# 5) podman 镜像层缓存（停机维护时）
podman system prune -f
```

> **注意**：pipeline 链路会把原始 `.s1p` / `.s2p` 归档为 `.s1p.gz` / `.s2p.gz`，
> 磁盘占用约为原 snp 文件的 10%–30%。若空间仍紧张，可临时关闭归档
> （`PIPELINE_COMPRESS_RAW=false` 后重启 worker），但不建议长期关闭。

一键清理脚本 `$REPO/scripts/cleanup.sh`：

```bash
#!/usr/bin/env bash
set -euo pipefail

LOG=/data3/aln/logs/cleanup.log
{
  echo "=== $(date '+%F %T') cleanup start ==="
  find /data3/aln/uploads -name '*.zip' -mtime +7 -print -delete | wc -l \
      | awk '{print "uploads zip removed:", $1}'
  find /data3/aln/exports -type f -mmin +1440 -print -delete | wc -l \
      | awk '{print "exports tmp removed:", $1}'
  find /data3/aln/logs -name '*.log' -mtime +14 -print -delete | wc -l \
      | awk '{print "old logs removed:", $1}'
  find /data3/aln/backups -name 'aln_*.sql.gz' -mtime +30 -print -delete | wc -l \
      | awk '{print "old backups removed:", $1}'
  echo "after cleanup:"; df -h /data3 | tail -1
  echo "=== done ==="
} >> "$LOG" 2>&1
```

cron：

```
30 1 * * * $REPO/scripts/cleanup.sh
```

### 5.8 CORS 报错（前端联调）

现状：后端只对 `http://localhost:5173`（Vite dev）和同源放行。

新增源（如同事的 IP 调试）：

```bash
# 编辑 backend/app/main.py 中的 CORSMiddleware allow_origins
# 加入 'http://10.x.x.x:5173' 之类
podman compose restart api
```

注意：`allow_origins=["*"]` + `allow_credentials=True` 会被浏览器拒，要避免。

### 5.9 容器一直重启

```bash
podman ps   # 看 STATUS 列里的 "Restarting"
podman inspect aln-api -f '{{.RestartCount}}'
```

抓最近一次崩前的日志：

```bash
podman logs --tail 200 aln-api
podman logs --tail 200 aln-worker
```

如果日志看不到崩前堆栈（被截断），手动启动一份不带 restart 策略的临时容器调试：

```bash
podman run -it --rm --entrypoint sh \
  --env-file $REPO/.env \
  -v /data3/aln:/data \
  --network aln_default \
  localhost/deploy_api:latest

# 进容器后手动跑：
# uvicorn app.main:app --host 0.0.0.0 --port 8000
# 看 fatal 是什么
```

最常见原因：

- `.env` 里少了字段（如 `POSTGRES_PASSWORD`）：补上后 `podman compose up -d --force-recreate api`。
- Alembic 迁移不一致：`podman compose run --rm api alembic current` 看版本，`alembic upgrade head` 推到最新。
- 镜像里代码跟 DB schema 对不上：rebuild 镜像。

---

## 6. 数据库常用查询（cheat sheet）

进 PG：

```bash
podman exec -it aln-postgres psql -U aln aln
```

```sql
-- 1) 各批次概况
SELECT batch_no, device_count, deembedded, uploaded_at
FROM batches
ORDER BY uploaded_at DESC;

-- 2) 找某个 mark 在所有批次的分布
SELECT b.batch_no, COUNT(*) AS n
FROM devices d JOIN batches b ON b.id = d.batch_id
WHERE d.mark = 'A1-1'
GROUP BY b.batch_no
ORDER BY b.batch_no;

-- 3) 各批次 Pass 率
SELECT b.batch_no,
       COUNT(*) AS total,
       SUM(CASE WHEN d.pf='Y' THEN 1 ELSE 0 END) AS pass,
       ROUND(100.0 * SUM(CASE WHEN d.pf='Y' THEN 1 ELSE 0 END) / COUNT(*), 2) AS pass_pct
FROM devices d JOIN batches b ON b.id = d.batch_id
GROUP BY b.batch_no
ORDER BY b.batch_no;

-- 4) 任务队列健康
SELECT status, COUNT(*)
FROM upload_tasks
GROUP BY status;

-- 5) 最近 24 小时失败任务详情
SELECT id, batch_no, error_msg, started_at, finished_at
FROM upload_tasks
WHERE status='failed' AND started_at > now() - INTERVAL '24 hours'
ORDER BY started_at DESC;

-- 6) 卡住的 running 任务（超过 30 分钟没结束）
SELECT id, batch_no, progress_pct, progress_msg, started_at,
       now()-started_at AS duration
FROM upload_tasks
WHERE status='running' AND started_at < now() - INTERVAL '30 min';

-- 7) 单批次器件数 vs 表中实际行数（一致性校验）
SELECT b.batch_no, b.device_count AS recorded, COUNT(d.id) AS actual
FROM batches b LEFT JOIN devices d ON d.batch_id = b.id
GROUP BY b.id, b.batch_no, b.device_count
HAVING b.device_count <> COUNT(d.id);

-- 8) 表占用 Top 10
SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) AS total,
       pg_size_pretty(pg_relation_size(relid)) AS table_size
FROM pg_catalog.pg_statio_user_tables
ORDER BY pg_total_relation_size(relid) DESC
LIMIT 10;

-- 9) 索引使用情况（idx_scan=0 的可考虑删）
SELECT relname, indexrelname, idx_scan, pg_size_pretty(pg_relation_size(indexrelid))
FROM pg_stat_user_indexes
ORDER BY idx_scan;

-- 10) 跨批次工艺族 Qs 中位数（趋势监控）
SELECT b.batch_no,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY d.qs) AS qs_p50,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY d.k2eff_pct) AS k2_p50
FROM devices d JOIN batches b ON b.id = d.batch_id
WHERE d.eg = 0.5 AND d.fl = 0.0 AND d.pf = 'Y'
GROUP BY b.batch_no, b.uploaded_at
ORDER BY b.uploaded_at;

-- 11) 安全删除某个批次（连带 devices 走 ON DELETE CASCADE）
BEGIN;
SELECT batch_no, device_count FROM batches WHERE batch_no = 'T8901P.01';  -- 二次确认
DELETE FROM batches WHERE batch_no = 'T8901P.01';
-- 确认无误才 COMMIT，否则 ROLLBACK
COMMIT;
```

---

## 7. 安全 / 访问控制

### 7.1 当前状态

- **裸开**：无登录、无审计，仅靠所内网隔离。
- 监听：postgres / redis / api 全部 bind 在 `127.0.0.1`，外部访问不到。
- nginx 8080 是唯一对外端口。

### 7.2 紧急锁服务

需要立刻禁外部访问（如发现异常流量、临时维护）：

```bash
# 方法 1：firewalld
sudo firewall-cmd --remove-port=8080/tcp
# 恢复：
sudo firewall-cmd --add-port=8080/tcp

# 方法 2：直接停 nginx（API/worker 仍可用，便于本机操作）
podman compose stop nginx
# 恢复：
podman compose start nginx

# 方法 3：iptables 临时挡（重启失效）
sudo iptables -I INPUT -p tcp --dport 8080 -j DROP
# 撤销：
sudo iptables -D INPUT -p tcp --dport 8080 -j DROP
```

### 7.3 改 PostgreSQL 密码

容器的环境变量在创建时被烧进 PG 用户表，**改 .env 后单纯 restart 不生效**，必须 alter user + 重建容器。

```bash
# 1. 在 PG 里改用户密码
NEW_PWD='new_strong_password_here'
podman exec aln-postgres psql -U aln -c "ALTER USER aln WITH PASSWORD '${NEW_PWD}';"

# 2. 同步 .env
sed -i.bak "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${NEW_PWD}|" $REPO/.env

# 3. 重建依赖该密码的容器（api/worker 通过 DATABASE_URL 拼接，必须重启读 .env）
cd $REPO/deploy
podman compose up -d --force-recreate api worker

# 4. 验证
curl -s http://localhost:8080/api/health
```

postgres 容器本身不需要重建——`POSTGRES_PASSWORD` 仅在首次初始化 pgdata 时使用。

---

## 8. 关键文件清单

| 路径 | 作用 | 备份重要性 |
|---|---|---|
| `$REPO/.env` | 敏感配置（密码、端口） | 高，写入运维记录 |
| `$REPO/deploy/docker-compose.yml` | 编排定义 | 跟代码 git 走 |
| `$REPO/deploy/nginx/default.conf` | 反代 / SSE / 上传超时 | 跟代码 git 走 |
| `$REPO/backend/alembic/versions/` | DB 迁移脚本 | 跟代码 git 走 |
| `/data3/aln/pgdata/` | PostgreSQL 数据，**不可恢复**重要数据 | 极高，每日 dump |
| `/data3/aln/redis/` | Redis AOF（任务队列） | 中等，丢了任务要重传 |
| `/data3/aln/uploads/` | 用户原始 zip（7 天保留） | 低 |
| `/data3/aln/files/` | 解压后的 `.s1p` / `.s2p`，pipeline 处理后以 `.gz` 归档 | 中等，rsync 到 NAS |
| `/data3/aln/mappings/` | 上传的 mapping xlsx | 中等 |
| `/data3/aln/exports/` | 导出临时文件 | 无 |
| `/data3/aln/logs/` | 应用日志 | 无（保留 14 天） |
| `/data3/aln/backups/` | 数据库备份 | 高，自身就是备份 |

---

## 9. 常用命令速查表

| 任务 | 命令 |
|---|---|
| 看所有容器状态 | `podman compose ps` |
| 看容器资源占用 | `podman stats --no-stream` |
| 看 api 实时日志 | `podman logs aln-api --tail 100 -f` |
| 看 worker 日志 | `podman logs aln-worker --tail 200` |
| 看 nginx 访问日志 | `podman logs aln-nginx --tail 100` |
| 进 api 容器 | `podman exec -it aln-api bash` |
| 进 PG | `podman exec -it aln-postgres psql -U aln aln` |
| 进 Redis | `podman exec -it aln-redis redis-cli` |
| 重启某服务 | `podman compose restart <svc>` |
| 全栈重启 | `podman compose down && podman compose up -d` |
| 重建镜像并起 | `podman compose build api worker && podman compose up -d api worker` |
| 跑 DB 迁移 | `podman compose run --rm api alembic upgrade head` |
| DB 备份 | `podman exec aln-postgres pg_dump -U aln aln \| gzip > /data3/aln/backups/aln_$(date +%F).sql.gz` |
| DB 恢复 | `gunzip -c xxx.sql.gz \| podman exec -i aln-postgres psql -U aln aln` |
| 看 Celery 队列长度 | `podman exec aln-redis redis-cli LLEN celery` |
| Ping worker | `podman exec aln-worker celery -A app.workers inspect ping` |
| 健康检查 | `curl -s http://localhost:8080/api/health` |
| 看磁盘 | `df -h /data3 && du -sh /data3/aln/*` |
| 看占 15432 端口的进程 | `ss -tlnp \| grep 15432` |
| 强删容器 | `podman rm -f <container_name>` |
| 清理 podman 镜像缓存 | `podman system prune -f` |
| 把 nginx 临时下线 | `podman compose stop nginx` |
| 改密码后强制重建 | `podman compose up -d --force-recreate api worker` |

---

## 10. 联系与升级

值班期间遇到本手册未覆盖的故障：

1. 先 `podman compose ps` + `podman logs <container> --tail 500` 收集证据。
2. 搜 `upload_tasks` 表的 `error_msg` 关键词。
3. 在团队群贴日志片段（**注意去掉 .env 里的密码**）。
4. 若涉及数据丢失风险，立即 `podman compose stop api worker nginx` 冻结写入，再分析。

排查记录写入 `/data3/aln/logs/oncall.md`，便于下次复现。
