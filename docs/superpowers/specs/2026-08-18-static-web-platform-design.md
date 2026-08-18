# 静态网页版数据平台设计（v1）

> 日期：2026-08-18。状态：已确认方案（用户选定方案 A + 格均值谱叠加 + 新建专用仓库）。
> 目标：把数据平台做成永久在线的静态网站，无需作者电脑挂后端；数据随打包命令同步；代理模型浏览器端 ONNX 推理。

## 1. 背景与范围

现有平台：FastAPI + PostgreSQL + Celery + React，需常驻后端。本次改造为**纯静态站点**：

- 保留页面：仪表盘、数据分析、批次管理（只读）、批次详情（只读）、对照表、工艺、代理模型
- 移除：上传、任务、阻抗曲线页、原文件查看、一切写操作（删除/重命名/重处理按钮）
- 数据来源：构建时从作者电脑 `~/.aln-data/`（sqlite + 训练产物）快照
- 部署：GitHub Pages，新建专用仓库（如 `aln-data-web`），gh-pages 分支

## 2. 总体架构

```
build_web.py（作者电脑，一条命令）
  ├─ ① export_web_snapshot.py   sqlite → Parquet/JSON 快照
  ├─ ② export_surrogate_onnx.py torch → ONNX + 数值对齐校验
  ├─ ③ VITE_STATIC=1 npm run build（前端静态变体）
  └─ ④ deploy：git worktree gh-pages → commit → push（--no-deploy 跳过）
```

运行时零后端：静态文件 + DuckDB-WASM（查 Parquet）+ onnxruntime-web（模型推理），
两者均懒加载（进入对应页面才下载 WASM）。

## 3. 数据快照（export_web_snapshot.py）

输出到 `frontend/public/webdata/`（随构建进 dist）：

| 文件 | 内容 | 体积上限 |
|---|---|---|
| `devices.parquet` | devices 全参数列 + batch_no（JOIN batches 拍平），zstd 压缩 | ~6MB |
| `batches.json` | 批次列表+详情字段 | <1MB |
| `mappings.json` | 对照表 + entries | <1MB |
| `process.json` | 工艺页 steps + 行数据 | <1MB |
| `stats.json` | 仪表盘统计（预计算，等价 /api/stats） | KB |
| `cell_means.parquet` | 每结构格 (eg,fl,area,pf) 均值谱 1001 点（源自 surrogate_data/dataset.npz 的 full_z 按格聚合） | ~2.3MB |
| `baseline.json` | BaselineTable（pfs/areas/params_base/z_base_full） | ~200KB |
| `surr_meta.json` | 归一化统计 + 频率轴 + k（surrrogate meta.json 的子集） | KB |

快照脚本只做读库 + 聚合 + 写文件；任一源缺失（如 npz 不存在）→ 该文件跳过并在
`manifest.json` 记录 `missing: [...]`，前端对应功能降级而非报错。

## 4. 前端静态变体

- 构建开关：`VITE_STATIC=1`。
  - Sidebar 隐藏：上传、任务、阻抗曲线；页面内写操作按钮（删除批次/删除对照表/重处理等）隐藏
  - `client.ts`：`isStatic` 分支，所有请求改走 `src/api/static/` 适配层
- 适配层 `src/api/static/index.ts`：与 `endpoints.ts` 同签名导出：
  - 参数查询类（listBatchDevices/queryDevices/queryAggregate/queryFields/getStats 等）→ DuckDB-WASM 对 `devices.parquet` 执行等价 SQL
  - 静态信息类（listBatches/getBatch/listMappings/listMappingEntries/getProcessSteps/getProcessStep）→ fetch 对应 JSON
- DuckDB-WASM 初始化：懒单例；初始化失败 → 数据分析页显示错误卡片，其他页面不受影响
- 代理模型页：静态模式下推理改走 `src/ml/surrogateOnnx.ts`（见 §5），"同格真值叠加"改为**格均值谱单条**（cell_means.parquet，按需 DuckDB 查询）

## 5. 代理模型 ONNX 化

### 5.1 导出（export_surrogate_onnx.py）

- Stage 1 `BaselineResidualMLP`：单次导出双输出（params/sparse），动态 batch；调用方两遍前向语义不变
- Stage 2 `SparseReconstructor`：静态形状（K=256，N=1001），opset ≥ 17
- 导出即校验：onnxruntime-python vs PyTorch，100 组真实数据输入，逐元素 |diff| < 1e-3（归一化空间），不通过则构建失败
- 产物：`frontend/public/webdata/stage1.onnx` / `stage2.onnx`

### 5.2 浏览器推理（src/ml/surrogateOnnx.ts）

TS 移植的周边逻辑（每个都配 vitest 与 Python 黄金值对拍）：

1. 输入归一化（surr_meta.json 统计）
2. 确定性采样位置（移植 `deterministic_sample_positions`，含配额重分配与非法 fs/fp 回退）
3. 基准表查找（pf 最近 + 面积维 dB 域线性插值，移植 `BaselineTable.lookup`）
4. cubic 样条基线（自然三次样条，对齐 scipy CubicSpline 语义）
5. 两遍 Stage 1 → 采样位置 → Stage 2 → 反归一化（onnxruntime-web，wasm 后端）

### 5.3 精度验收

- 单元：上述 2/3/4 与 Python 实现对拍（1e-6 级）
- 端到端：静态站构建后 Playwright 实测——20 组格内输入，网页预测谱 vs Python `SurrogatePredictor` 全谱 |diff| < 0.05 dB

## 6. 构建与部署（build_web.py）

```bash
backend/.venv/bin/python build_web.py              # 构建 + 部署
backend/.venv/bin/python build_web.py --no-deploy  # 只构建，本地 preview 验证
```

部署流程：`git worktree add .web-deploy gh-pages`（无则 `git checkout --orphan` 建分支）→
rsync dist → commit（带数据日期）→ push 到专用仓库。仓库 URL 与 token：

- 环境变量 `ALN_WEB_REPO`（如 `https://github.com/bzJing925/aln-data-web.git`，有默认值可改）
- token 只从环境变量 `GITHUB_TOKEN` 读，不写进任何文件；push 用 `https://x-access-token:${GITHUB_TOKEN}@...` 一次性 URL（不落 git config）

## 7. 错误处理

- 快照缺失/manifest missing → 对应页面显示"数据未同步"卡片，不白屏
- DuckDB-WASM / onnxruntime 加载失败 → 页面级错误卡片 + 重试按钮
- ONNX 对齐校验失败 → 构建中止，不部署

## 8. 测试策略

- Python：快照导出（行数/字段/聚合正确性）、ONNX 对齐（§5.1）
- 前端 vitest：采样位置/基准插值/样条 vs Python 黄金值；适配层 SQL 与后端语义抽样对比
- 端到端：构建产物本地起静态服务 + Playwright（§5.3 + 各页面可达性冒烟）

## 9. 验收标准

1. 静态站所有保留页面无后端可用（断网仅留静态文件服务即可浏览）
2. 代理模型页端到端精度 < 0.05 dB（§5.3）；单次推理 < 100ms
3. 首屏加载 < 3s（WASM 懒加载不计入）
4. `build_web.py` 一条命令完成数据更新 + 部署，全过程 < 5 分钟

## 10. 风险与开放点

| 风险 | 应对 |
|---|---|
| GitHub Pages 公开可访问（即使私有仓库，Pages URL 默认公开） | 用户已知情接受；如后续需管控，迁移到带鉴权的内网静态托管（构建产物不变） |
| Transformer ONNX 导出算子兼容性 | 导出即对齐校验兜底；若失败，降级方案：预计算全部 480 格预测谱（~4MB）网页零推理 |
| onnxruntime-web wasm 体积（~6MB） | 懒加载 + gzip；仅代理模型页触发 |
| TS 移植逻辑的数值漂移 | 黄金值对拍测试锁定；任何一边改动需同步 |
| 数据更新靠手动跑命令 | 文档写明；后续可选 GitHub Actions 定时提醒 |
