# 性能架构优化方案

**版本**：v1.0  
**日期**：2026-06-02  
**状态**：已实施物化视图，其余为长期规划

---

## 背景

平台已完成 9 项代码级性能优化（缓存、降采样、并行化、索引、流式导出、连接池调优等）。当数据量从 50 万行增长到数百万行时，需要架构级手段兜底。

本文档描述四项架构级优化：**物化视图**（已实施）、**表分区**（方案就绪）、**读写分离**（方案就绪）、**前端虚拟滚动**（评估中）。

---

## 一、物化视图（Materialized Views）✅ 已实施

### 1.1 设计目标

避免每次打开 BatchDetail 页面时对 `devices` 表做实时 `AVG` / `PERCENTILE_CONT` / `COUNT` 聚合。

### 1.2 已创建的视图

```sql
CREATE MATERIALIZED VIEW mv_batch_stats AS
SELECT
    b.id AS batch_id,
    b.batch_no,
    d.wafer,
    COUNT(*) AS total_count,
    COUNT(*) FILTER (WHERE d.pf = 'Y') AS pass_count,
    ROUND(AVG(d.fs_ghz)::numeric, 6) AS avg_fs_ghz,
    ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY d.fs_ghz))::numeric, 6) AS median_fs_ghz,
    ROUND(AVG(d.qs)::numeric, 3) AS avg_qs,
    ROUND(AVG(d.k2eff_pct)::numeric, 4) AS avg_k2eff_pct,
    ROUND(MIN(d.fs_ghz)::numeric, 6) AS min_fs_ghz,
    ROUND(MAX(d.fs_ghz)::numeric, 6) AS max_fs_ghz
FROM devices d
JOIN batches b ON b.id = d.batch_id
GROUP BY b.id, b.batch_no, d.wafer;

CREATE UNIQUE INDEX idx_mv_batch_stats_pk ON mv_batch_stats (batch_id, wafer);
```

### 1.3 刷新策略

- **入库后自动刷新**：`process_batch.py` 在 Celery 任务完成入库后执行
  ```sql
  REFRESH MATERIALIZED VIEW CONCURRENTLY mv_batch_stats;
  ```
  使用 `CONCURRENTLY` 避免锁表（要求物化视图有唯一索引，已满足）。
- **定时刷新**：可配 `pg_cron` 每小时兜底刷新一次。
- **手动刷新**：运维需要时执行 `REFRESH MATERIALIZED VIEW CONCURRENTLY mv_batch_stats;`

### 1.4 使用方式

`batches.py::get_batch` 优先从 `mv_batch_stats` 读取统计，查询失败时静默回退到实时聚合。

### 1.5 效果预估

| 指标 | 优化前 | 优化后 |
|---|---|---|
| BatchDetail 统计查询 | ~200 ms（50 万行实时聚合） | ~5 ms（物化视图索引扫描） |

---

## 二、表分区（Table Partitioning）📋 方案就绪

### 2.1 适用时机

当 `devices` 表行数 **≥ 500 万** 时启用。当前 50 万行无需分区，但方案已就绪。

### 2.2 分区策略

**按 `batch_id` 范围分区**（Range Partitioning）。理由：
- 90% 查询带 `batch_id` 筛选（单批次列表、版图分布）
- 历史批次只读，新批次写入，分区裁剪效果最佳
- 删除旧批次时可 `DROP PARTITION` 秒级完成

### 2.3 DDL 方案

```sql
-- 1. 创建分区父表（与原表结构相同）
CREATE TABLE devices_partitioned (
    LIKE devices INCLUDING ALL
) PARTITION BY RANGE (batch_id);

-- 2. 为已有批次创建分区（每批次一个分区，或每 10 个批次一个分区）
CREATE TABLE devices_p1 PARTITION OF devices_partitioned
    FOR VALUES FROM (1) TO (100000);

-- 3. 创建默认分区（接收新 batch_id）
CREATE TABLE devices_default PARTITION OF devices_partitioned DEFAULT;

-- 4. 迁移数据（需要停服窗口）
INSERT INTO devices_partitioned SELECT * FROM devices;

-- 5. 重命名表（原子操作）
ALTER TABLE devices RENAME TO devices_old;
ALTER TABLE devices_partitioned RENAME TO devices;

-- 6. 重建外键和索引（PostgreSQL 分区表自动继承部分索引）
```

### 2.4 应用层改动

无需改动。SQLAlchemy 2.0 对声明式分区表完全透明，ORM 查询自动走分区裁剪。

### 2.5 实施风险

- 需要**停服窗口**（数据迁移时间取决于数据量，50 万行约 5–10 分钟）
- 分区后部分索引行为变化（全局唯一索引需包含分区键）
- 建议数据量破 500 万后再实施

---

## 三、读写分离（Read/Write Splitting）📋 方案就绪

### 3.1 适用时机

多用户并发查询 + 大批量上传同时进行，导致主库 CPU/连接数吃紧。

### 3.2 架构

```
┌─────────────┐     写入/事务 ──▶ ┌──────────┐
│  FastAPI    │                   │  PG 主库  │
│  Celery     │ ──同步复制────▶   │          │
│  Worker     │                   │          │
└─────────────┘     只读查询 ──▶ ┌──────────┐
                                  │  PG 从库  │
                                  └──────────┘
```

### 3.3 配置方案

在 `config.py` 中新增只读数据库 URL：

```python
class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+psycopg://aln:aln@localhost:5432/aln"
    DATABASE_READONLY_URL: str | None = None  # 未配置时查询也走主库
```

在 `db.py` 中创建只读引擎：

```python
_readonly_engine = None

def get_readonly_engine():
    global _readonly_engine
    if _readonly_engine is None and _settings.DATABASE_READONLY_URL:
        _readonly_engine = create_engine(_settings.DATABASE_READONLY_URL, ...)
    return _readonly_engine or engine
```

在查询路由中显式使用只读 session：

```python
# app/api/query.py、app/api/batches.py 等只读接口
@router.post("/devices")
def query_devices(req: QueryRequest, db: DbReadonlySession) -> QueryResponse:
    ...
```

### 3.4 实施步骤

1. 部署 PostgreSQL 物理从库（流复制，`streaming replication`）
2. 配置 `.env` 中的 `DATABASE_READONLY_URL`
3. 修改查询路由使用只读 session
4. 监控从库复制延迟（`pg_stat_replication`）

### 3.5 注意事项

- 从库有复制延迟（通常 < 1 秒），上传后立即可见查询可能走主库
- 物化视图刷新、Celery 任务等写入操作**必须**走主库

---

## 四、前端虚拟滚动（Virtual Scrolling）📋 评估中

### 4.1 当前状态

- **BatchDetail 器件列表**：已有分页（50/100/200 行/页），200 行原生表格在现代浏览器中渲染流畅
- **Explore 页面**：无表格，纯 Plotly.js 图表。Plotly `scattergl` 本身用 WebGL，已处理 5 万点

### 4.2 评估结论

当前数据量下**不需要**引入 `react-window` 等虚拟滚动库：
- 分页已解决表格大数据问题
- Plotly WebGL 已解决散点图大数据问题
- 引入虚拟滚动会增加代码复杂度，收益有限

### 4.3 未来触发条件

当以下场景出现时才考虑：
- BatchDetail 分页从 200 行/页提升到 2000 行/页
- Explore 新增数据表格视图（非图表），且需要显示 > 1000 行

---

## 五、实施优先级建议

| 阶段 | 优化项 | 触发条件 | 预估工时 |
|---|---|---|---|
| 当前 ✅ | 物化视图 | 已完成 | — |
| 阶段 1 | 读写分离 | 并发用户 > 10 或上传与查询频繁冲突 | 1 天 |
| 阶段 2 | 表分区 | devices 行数 > 500 万 | 1 天（+ 停服窗口） |
| 观望 | 前端虚拟滚动 | 分页不能满足需求时 | 0.5 天 |

---

## 六、监控指标

建议长期监控以下指标，作为触发架构级优化的依据：

| 指标 | 健康阈值 | 告警阈值 |
|---|---|---|
| `devices` 表行数 | < 100 万 | > 500 万 |
| `/query/devices` P99 延迟 | < 500 ms | > 2 s |
| `/batches/{no}` P99 延迟 | < 100 ms | > 500 ms |
| PG 主库 CPU 使用率 | < 50% | > 80% |
| PG 活跃连接数 | < pool_size | > max_overflow |
| Redis 缓存命中率 | > 30% | < 10% |

---

> 本文档与代码同步维护。实施任何架构级优化前，请先跑 `scripts/benchmark.py` 确认瓶颈。
