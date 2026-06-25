# 边压缩边计算（Compress-While-Calculate）设计规格

**日期**：2026-06-25  
**状态**：已批准，待实现  
**测试数据**：`/Users/jingbozuo/Projects/#2.zip`（约 10.5 GB）

---

## 1. 背景与目标

当前上传流程把 ZIP 完整解压到 `files/<batch_no>/` 后，再由 `compute_batch` 逐文件提参。对于含 de-embedding 的大批次（如 #2.zip），存在以下问题：

1. **磁盘占用高**：10 GB 的 zip 解压后可能产生数十 GB 原始 `.s1p/.s2p`。
2. **I/O 与 CPU 串行**：解压阶段纯 I/O，计算阶段纯 CPU，二者没有重叠。
3. **De-embedding 与计算脱节**：当前代码在启用 de-embed 时禁止直接处理 `.s2p` DUT。

本设计目标：

- 使用第三方解压软件（7z/p7zip）加速解压。
- **边解压边计算**：DUT 文件落地后立即拆分、去嵌、提参、入库。
- **边计算边压缩**：提参成功后立即把原始 `.s1p/.s2p` gzip 归档，降低磁盘占用。
- 保留原始 snp 数据（以压缩形式），支持后续下载/重算。

---

## 2. 术语

| 术语 | 说明 |
|------|------|
| DUT | 被测器件，即需要提参的 `.s1p/.s2p` 文件。 |
| 校准件 | OPEN / SHORT `.s2p`，用于 ShortOpen 去嵌。 |
| Pipeline | 新的 Celery 处理链路 `aln.pipeline_batch`。 |
| 消费者 | `ProcessPoolExecutor` 中的工作进程，负责单个 DUT 的完整处理。 |
| 原始 snp | 用户上传的、未去嵌的 `.s1p/.s2p` 文件。 |

---

## 3. 需求摘要

### 3.1 功能需求

1. 当上传批次满足 **de-embed 启用且 zip 内含 OPEN/SHORT 校准件** 时，自动走新链路 `pipeline_batch`。
2. 用 7z/p7zip 解压 zip（已存在能力）。
3. 解压过程中实时发现 DUT 文件并调度计算。
4. 对 `.s2p` DUT 自动拆分为 S11/S22，并用对应端口的 OPEN/SHORT 做 ShortOpen 去嵌。
5. 对去嵌后的 s1p（或原始 s1p）提取谐振参数并写入 `devices`。
6. 提参成功后，立即 gzip 压缩原始 `.s1p/.s2p`（保留 `.s1p.gz/.s2p.gz`）。
7. 实时更新 `UploadTask` 进度（SSE）。
8. 不含 de-embed 的批次保持原 `extract_batch → compute_batch` 链路，避免回归。

### 3.2 非功能需求

1. **可配置**：是否启用新链路、消费者数、扫描间隔、是否压缩、是否保留中间文件。
2. **可观测**：日志记录解压速度、处理速度、失败文件、磁盘占用变化。
3. **容错**：单个 DUT 失败不影响整体批次；解压失败则整体失败。
4. **可测试**：核心模块可单元测试；#2.zip 用于集成测试。

---

## 4. 方案选择

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| A. 分阶段并发流水线（推荐） | 7z 解压 + 文件扫描 + 消费者池并发处理；校准件先索引，DUT 边解压边计算。 | 改动可控，复用现有 7z/去嵌/提参代码，真正重叠 I/O 与 CPU。 | 需要文件扫描与并发同步。 |
| B. 单任务合并解压+计算 | 新 Celery 任务内部自己管理 7z 和多进程。 | 逻辑集中。 | 任务粒度大，重试成本高，偏离现有架构。 |
| C. zip 流式处理 | 直接从 zip 流式读取内容到内存，不落地或仅落地归档。 | 磁盘占用最小。 | 需让 skrf/去嵌/拆分支持流式输入，改造量巨大。 |

**最终选择：方案 A。**

---

## 5. 架构设计

### 5.1 总体数据流

```
上传接口
    │
    ▼
saved to uploads/YYYY-MM/<uuid>.zip
    │
    ▼
dispatch aln.pipeline_batch
    │
    ├──▶ 7z 解压到 files/<batch_no>/  （生产者线程）
    │         │
    │         ▼
    │    文件扫描器发现新文件
    │         │
    │         ▼
    │    校准件？ ──是──▶ 拆分 OPEN/SHORT → 建 CalibrationIndex
    │         │ 否
    │         ▼
    │    DUT 文件入队
    │         │
    │         ▼
    │    ProcessPoolExecutor 消费者
    │         │
    │         ▼
    │    s2p 拆分 → 匹配 open/short → deembed → extract_resonator_params
    │         │
    │         ▼
    │    gzip 原始 snp + 返回 device row
    │
    ▼
主线程批量写入 devices + 刷新 mv_batch_stats + 清理
```

### 5.2 与现有链路关系

- 新链路 `aln.pipeline_batch` **仅**在上传参数 `deembed=True` 且 zip 内识别到校准时启用。识别方式：上传服务对 zip 做一次轻量 listing（`zipfile.namelist()` 或 `7z l`），检查文件名是否含 OPEN/SHORT 关键字。
- 其他情况继续投递 `aln.extract_batch → aln.compute_batch`。
- 上传服务层 `upload_service.py` 负责根据条件选择链路。

---

## 6. 组件设计

### 6.1 组件清单

| 组件 | 文件 | 职责 |
|------|------|------|
| `pipeline_batch_task` | `backend/app/workers/pipeline_batch.py` | Celery 任务入口，协调各阶段。 |
| `StreamingExtractor` | `backend/app/workers/pipeline/extractor.py` | 封装 7z/p7zip，提供文件落地事件与进度回调。 |
| `CalibrationIndex` | `backend/app/workers/pipeline/calibration.py` | 拆分校准件并按方法建立匹配索引。 |
| `DutProcessor` | `backend/app/workers/pipeline/processor.py` | 单 DUT 处理：拆分→去嵌→提参→归档。 |
| `FileWatcher` | `backend/app/workers/pipeline/watcher.py` | 扫描目录，发现新 DUT，排除校准件，去重。 |
| 配置项 | `backend/app/config.py`（`Settings`） | 见第 10 节。 |

### 6.2 接口约束

- `extract_resonator_params`、`split_s2p_to_s1p`、`deembed` 保持文件路径接口不变，降低改造风险。
- 消费者函数为纯函数：输入 `(item, mapping_dict, wafer, cfg_dict)`，输出 `{"ok": bool, "row": dict|None, "error": str|None, "archived": list[str]}`。
- 数据库写入统一由主线程通过 `_bulk_insert_devices` / COPY 完成。

---

## 7. 并发模型

### 7.1 生产者-消费者

1. **解压生产者线程**：调用 7z 解压到目标目录。
2. **文件扫描器**：主线程每 `PIPELINE_SCAN_INTERVAL` 秒扫描目录，维护已处理集合，把新 DUT 入队。
3. **消费者池**：`ProcessPoolExecutor(max_workers=PIPELINE_WORKERS)`，每个进程处理一个 DUT。
4. **结束信号**：解压线程结束后向队列放入 sentinel，消费者消费完 sentinel 后退出。

### 7.2 同步点

- 校准件索引未就绪前，DUT 任务在队列中等待（主线程不提交到进程池，或消费者内部阻塞读取索引）。
- 推荐实现：**主线程**在校准件索引建立后才开始提交 DUT；扫描阶段发现的 DUT 先缓存到内部队列。

### 7.3 进程安全

- 消费者只读原始文件。
- 去嵌输出写到按端口区分的子目录（`S11_de/`、`S22_de/`），文件名含 DUT 名，不冲突。
- gzip 归档由消费者原地执行；同文件不会被两个消费者处理（已处理集合去重）。

---

## 8. 去嵌与压缩策略

### 8.1 去嵌流程

1. 解压中识别校准件（按现有 `parse_filename.is_calibration` 与各方法关键字）。
2. 校准件全部落地后，调用 `split_s2p_to_s1p` 拆分为 S11/S22，存入 `cal_S11/`、`cal_S22/`。
3. 用 `match_calibration` 为每个 DUT 端口匹配 open/short。
4. s2p DUT 处理：
   - 拆 DUT → `S11/`、`S22/`。
   - 去嵌 → `S11_de/`、`S22_de/`。
   - 对 `*_de.s1p` 提参。

### 8.2 压缩流程

- 提参成功后，消费者把**原始 snp**（`.s1p` 或 `.s2p`）gzip 为 `.s1p.gz` / `.s2p.gz`。
- 去嵌中间文件 `*_de.s1p` 默认删除（`PIPELINE_KEEP_DEEMBED_TEMP=False`）。
- `devices.s_param_path` 指向压缩后的路径。
- 现有 `/api/files/...` 下载接口需扩展：若请求 `.s1p.gz` 则返回 gzip 内容并设置 `Content-Encoding: gzip`；若请求 `.s1p` 则透明解压后返回。

---

## 9. 错误处理

| 场景 | 行为 |
|------|------|
| 解压失败 | 标记任务失败，清理 `files/<batch_no>/`，可选保留 zip 供排查。 |
| 校准件缺失 | 阶段 1 结束立即失败，提示"启用 de-embed 但未找到 OPEN/SHORT"。 |
| 单个 DUT 失败 | 记录失败信息，继续处理其余 DUT。 |
| 消费者异常 | 主线程捕获，未完成任务重新提交或整体失败。 |
| gzip 归档失败 | 记录 warning，不影响已入库结果。 |

---

## 10. 配置项

在 `backend/app/config.py` 的 `Settings` 中新增：

```python
class Settings(BaseSettings):
    # ... 现有配置 ...

    # 边压缩边计算流水线
    PIPELINE_ENABLED: bool = True          # 是否启用新链路
    PIPELINE_WORKERS: int = 0              # 消费者进程数；0 = os.cpu_count()
    PIPELINE_SCAN_INTERVAL: float = 1.0    # 文件扫描间隔（秒）
    PIPELINE_COMPRESS_RAW: bool = True     # 提参后是否 gzip 原始 snp
    PIPELINE_KEEP_DEEMBED_TEMP: bool = False  # 是否保留去嵌中间 *_de.s1p
```

---

## 11. 进度规划

| 阶段 | 进度范围 | 说明 |
|------|----------|------|
| 解压 | 0–30% | 由 7z 逐文件回调驱动。 |
| 校准件拆分与索引 | 30–35% | 校准件通常很少，很快完成。 |
| DUT 处理 | 35–95% | 按 `已处理 / max(已处理, 已扫描到的 DUT 总数)` 更新；解压结束时最终总数确定，进度自动回填。 |
| 入库、刷新视图、清理 | 95–100% | 汇总、刷新 `mv_batch_stats`、删除 zip/中间文件。 |

---

## 12. 测试策略

### 12.1 单元测试

- `tests/workers/pipeline/test_watcher.py`：模拟文件落地，验证 watcher 识别 DUT、排除校准件、去重。
- `tests/workers/pipeline/test_calibration.py`：验证 `CalibrationIndex` 在不同 `deembed_method` 下正确匹配 open/short。
- `tests/workers/pipeline/test_processor.py`：用临时 zip 构造 s2p DUT + OPEN/SHORT，验证完整流程及 gzip 归档。

### 12.2 集成测试

- 用 `/Users/jingbozuo/Projects/#2.zip` 做端到端测试（标记 `integration`）。
- 验证：任务成功、device 行数合理、原始 snp 已 gzip、磁盘占用下降、失败样本可追踪。

### 12.3 回归测试

- 无 de-embed 批次仍走原链路，运行现有 `tests/core` 与 API 测试。
- 确保 `extract_batch` / `compute_batch` 接口与行为不变。

---

## 13. 风险与回滚

| 风险 | 缓解措施 |
|------|----------|
| 新链路并发 bug 导致数据丢失 | 默认 `PIPELINE_ENABLED=False` 可在生产灰度开启；失败时重试走旧链路。 |
| gzip 后前端下载接口不兼容 | 同步扩展 `files.py` 支持透明 gzip 读取/解压返回。 |
| 去嵌匹配错误 | 复用现有 `match_calibration`，单元测试覆盖各方法。 |
| 7z 未安装 | 回退到现有 zipfile / unzip 逻辑，但失去速度优势。 |

---

## 14. 待实现清单

1. 创建 `backend/app/workers/pipeline/` 包及上述 4 个模块。
2. 实现 `pipeline_batch_task` 并注册到 Celery。
3. 修改 `upload_service.py` 根据 `deembed` 与校准件预判选择链路。
4. 扩展 `files.py` 下载接口支持 gzip。
5. 添加配置项。
6. 编写单元测试与集成测试。
7. 用 `#2.zip` 跑通集成测试。
8. 更新 `docs/api.md` / `docs/operations.md` 中关于上传与文件下载的说明。

---

## 15. 决策记录

- **2026-06-25**：用户确认采用"分阶段并发流水线"方案；保留原始 snp 并额外 gzip 压缩；需要在解压同时进行 de-embedding。
