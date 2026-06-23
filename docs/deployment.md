# 部署指南（Linux）

**版本**：v0.2
**部署方式**：Podman Compose
**目标机**：fineserver（Rocky Linux 8.10，Podman 4.9.4）

> Windows 同事请走 [`deployment-windows.md`](./deployment-windows.md)（Docker Desktop + PowerShell）。
> 本文里所有 `podman compose ...` 命令在 Docker Engine 20.10+ 上把 `podman` 换成 `docker` 即可，编排文件兼容。
>
> **路径约定**：下文所有 `$REPO` 指本仓库 clone 后的根目录。先 `export REPO=/your/path/to/aln-data` 一次，命令即可直接复制执行。
> `$DATA_ROOT` 同理（即 `.env` 中的 `DATA_ROOT`，默认 `/data3/aln`）。

---

## 1. 总览

5 个容器，通过 podman compose 编排：

| 容器 | 镜像 | 端口（127.0.0.1 绑定） | 数据 volume |
|---|---|---|---|
| postgres | `postgres:15-alpine` | 5432 | `/data3/aln/pgdata` |
| redis | `redis:7-alpine` | 6379 | `/data3/aln/redis` |
| api | 自构建（FastAPI） | 8000 | `/data3/aln` 整体挂载 |
| worker | 自构建（Celery） | — | `/data3/aln` 整体挂载 |
| nginx | `nginx:alpine` | 8080（对外） | 静态文件 + exports 只读 |

只有 nginx 暴露给外部，其余服务仅本机回环。

---

## 2. 首次部署步骤

### 2.1 准备数据目录（已完成）

```bash
sudo mkdir -p /data3/aln/{pgdata,redis,uploads,files,mappings,exports,logs}
sudo chown -R $USER:$USER /data3/aln
```

### 2.2 准备环境变量

```bash
cd $REPO
cp .env.example .env
# 编辑 .env，至少修改 POSTGRES_PASSWORD
```

### 2.3 拉取镜像 + 构建

```bash
cd $REPO/deploy
podman compose --env-file ../.env build
podman compose --env-file ../.env pull
```

首次构建会下载 ~600MB（PostgreSQL/Redis/Nginx）+ 自构建 api/worker 镜像（~800MB）。

### 2.4 启动数据服务（不含 api/worker）

```bash
podman compose --env-file ../.env up -d postgres redis
podman compose ps  # 检查健康
```

### 2.5 跑数据库迁移

```bash
podman compose --env-file ../.env run --rm api alembic upgrade head
```

### 2.6 启动应用层

```bash
podman compose --env-file ../.env up -d api worker nginx
podman compose ps
podman compose logs -f api worker  # 观察启动日志
```

### 2.7 健康检查

```bash
curl http://localhost:8080/api/health
# 期望: {"status":"ok","db":"ok","redis":"ok",...}
```

浏览器访问 `http://fineserver:8080`，看到上传页即成功。

---

## 3. 日常运维

### 3.1 查看日志

```bash
# 实时跟踪
podman compose logs -f api
podman compose logs -f worker --tail 200

# 单容器
podman logs aln-api --tail 100
```

### 3.2 重启服务

```bash
# 单服务
podman compose restart api

# 全部
podman compose restart
```

### 3.3 进容器调试

```bash
podman exec -it aln-api bash
podman exec -it aln-postgres psql -U aln
```

### 3.4 升级代码

```bash
cd $REPO
git pull
cd deploy
podman compose build api worker
podman compose up -d api worker
# 数据库迁移（如有）
podman compose run --rm api alembic upgrade head
```

### 3.5 升级前端

```bash
cd $REPO/frontend
npm ci
npm run build         # 输出到 frontend/dist
podman compose restart nginx  # nginx 直接读 dist 卷，重启即可
```

### 3.6 停机维护

```bash
podman compose stop                    # 停止
podman compose down                    # 停止 + 移除容器（数据保留）
podman compose down -v                 # ⚠️ 危险：连匿名卷一起删（命名 volume 不影响）
```

---

## 4. 备份与恢复

### 4.1 数据库备份

```bash
# 每日 cron
podman exec aln-postgres pg_dump -U aln aln | gzip > /data3/aln/backups/aln_$(date +%F).sql.gz
```

cron 示例（`crontab -e`）：

```
0 2 * * * podman exec aln-postgres pg_dump -U aln aln | gzip > /data3/aln/backups/aln_$(date +\%F).sql.gz
0 3 * * 0 find /data3/aln/backups -name 'aln_*.sql.gz' -mtime +30 -delete
```

### 4.2 数据库恢复

```bash
gunzip -c /data3/aln/backups/aln_2026-05-09.sql.gz | podman exec -i aln-postgres psql -U aln aln
```

### 4.3 文件备份

`/data3/aln/files/` 是上传的原始数据，体积大但**可由用户重新上传重建**，重要性中等。如需备份，建议挂 NAS 用 `rsync`：

```bash
rsync -av --delete /data3/aln/files/ /mnt/nas/aln-files/
```

---

## 5. 监控

### 5.1 内置健康检查

- `GET /api/health` — DB / Redis 连通性 + 磁盘空间
- `GET /api/stats` — 批次/器件/任务总览
- `podman compose ps` — 容器状态

### 5.2 磁盘告警

```bash
# 简单 cron 告警
0 */6 * * * df /data3 | awk 'NR==2 && $5+0>85 {print "/data3 disk usage: " $5}' | mail -s "ALN disk alert" admin@example.com
```

### 5.3 日志归档

容器 stdout 默认走 podman 自管的日志驱动，可配置 `--log-opt max-size=100m` 限制。生产环境可考虑接入 journald 或外部 ELK。

---

## 6. 常见问题

### 6.1 Podman compose 报"network already exists"

```bash
podman network prune
podman compose up -d
```

### 6.2 PostgreSQL 启动失败：`/var/lib/postgresql/data/pgdata` 权限

PostgreSQL 容器用户是 999/999（postgres uid in alpine）。确保 `/data3/aln/pgdata` 属主一致：

```bash
sudo chown -R 999:999 /data3/aln/pgdata
# 或者用 podman 无 root 模式时改 :Z 标签
```

如果用的是 podman rootless，注意 SELinux 标签 `:Z`。本机 SELinux 状态用 `getenforce` 查。

### 6.3 worker 处理任务失败但 api 正常

通常是 worker 容器没拿到最新镜像 / .env 改了没 rebuild。

```bash
podman compose build --no-cache worker
podman compose up -d worker
```

### 6.4 上传卡在 50%

检查 nginx 的 `client_max_body_size` 和 `proxy_read_timeout`（已在 `nginx/default.conf` 设置）。
检查 podman 容器的 ulimit。

### 6.5 大表查询慢

```bash
podman exec -it aln-postgres psql -U aln aln -c "EXPLAIN ANALYZE SELECT ..."
```

观察是否走了预期索引；必要时 `VACUUM ANALYZE devices;`。

---

## 7. 卸载 / 重置

### 7.1 完全清理（保留数据）

```bash
cd $REPO/deploy
podman compose down
```

容器删除，数据 volume `/data3/aln/*` 保留。

### 7.2 完全重置（含数据，⚠️ 不可恢复）

```bash
podman compose down
sudo rm -rf /data3/aln/{pgdata,redis,uploads,files,exports}/*
# 保留 logs/ 用于排查
```

---

## 8. 端口与防火墙

仅一个对外端口：**8080**（Nginx）。

如果希望对外用 80/443：
- 改 `.env` 的 `NGINX_PORT=80`
- 或在 host nginx 上做二级反代 + HTTPS（推荐）

防火墙：

```bash
sudo firewall-cmd --add-port=8080/tcp --permanent
sudo firewall-cmd --reload
```

---

## 9. 待补充

- HTTPS 配置（生产环境上线时再加，需要证书来源信息）
- 容器 OOM 限制（首次跑后看实际内存占用再加 `mem_limit`）
- 滚动更新策略（v1 单机部署不需要）
