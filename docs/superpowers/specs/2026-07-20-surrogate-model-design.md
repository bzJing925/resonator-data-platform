# 谐振器代理模型（Surrogate Model）设计规格

日期：2026-07-20（v2：同步 aln-data2 后修订 §5，提取流程复用 `nn_export` / `sparse_sampler` 构件）
状态：已评审（头脑风暴阶段确认）

## 1. 背景与目标

基于已上传的谐振器测试数据，训练一个**代理模型**：输入器件结构参数，预测性能参数与完整阻抗谱 Z11(dB)，用于"预测未流片设计"的离线分析。

- **输入**：EG、FL、面积（area_um2）；预留 AG 第 4 维（当前数据 AG 全空，恒 0 + 有效位 mask）
- **输出**：7 个性能参数（fs、fp、kt2、Qs、Qp、qs_bodeq、qp_bodeq）+ 1001 点 Z11(dB) 全谱
- **交付形态**：纯离线（训练脚本 + 评估报告 + CLI 推理），不改 Web 平台；精度验证达标后再决定是否做 UI 集成

## 2. 已确认的关键决策

| 决策点 | 结论 |
|---|---|
| 输入维度 | 3 维 (EG, FL, 面积)，代码预留 AG 第 4 维 |
| 建模范围 | 只用批次 `#2`（4–7 GHz），不跨频段 |
| 交付形态 | 先离线验证再定 UI 集成 |
| 架构 | 方案 A：两阶段级联（Stage 1 代理模型 + Stage 2 稀疏重建） |
| Stage 1 结构 | 基准锚定残差：eg=0/fl=0 单元为基准，网络只学残差 |
| 训练数据提取 | 复用 aln-data2 的 `nn_export` / `sparse_sampler` 构件；新增确定性位置规则、清洗、基准表、划分（v2） |
| 成功标准 | 全谱 RMSE ≤ 0.5 dB（test 网格单元） |

## 3. 数据现状（已核实）

桌面版本地数据：`~/.aln-data/aln-data.db`（sqlite）+ `~/.aln-data/files/`。

- 2 个批次、50,411 条器件记录，全部有 `s_param_path`
- **批次 `#2`（主训练集）**：42,900 条；38,400 条 eg/fl/area_um2 完整；EG(5 档) × FL(8 档) × 面积(12 档) = **480 满网格**，每格约 80 样本；频段 4–7 GHz（原始 .s2p 12001 点）
- **批次 `01`（不用）**：无 area_um2，频段 3–20 GHz，判断为不同 band 产品
- `ag` 列 50,411 条全 NULL（mapping 源表 1317 条仅 10 条有值）——源头缺数据，非导入 bug
- 性能参数列齐全：`fs_ghz/fp_ghz/k2eff_pct/qs/qp/qs_bodeq/qp_bodeq` 全部非空
- 结构参数只在数据库（mapping 导入），文件名里没有，**提取必须走 DB ⋈ 文件**
- 基准组（eg=0, fl=0）：1,200 器件，12 面积档 × 100；格间 fs 均值仅 4.65–4.85 GHz，证实"曲线形状相似、频点偏移小"
- **离群值坑**：个体器件 fs 散布 4.0–6.15 GHz（Fail 器件提取抓到杂波/谐波），必须清洗

## 4. 架构总览

```
离线管线（backend/scripts/）
  extract_surrogate_dataset.py   sqlite/PG + files/ → dataset.npz（含划分、基准表）
  train_surrogate_stage1.py      dataset.npz → stage1.pt
  finetune_stage2.py             现有 reconstructor 微调 → stage2.ft.pt
  evaluate_surrogate.py          test 网格单元精度报告 + 图
  predict_spectrum.py            CLI：--eg --fl --area → 性能参数 + 全谱图/CSV

推理链路
  (EG, FL, area) → Stage 1（基准+残差）→ 7 性能参数 + 256 稀疏点
                 → Stage 2（reconstructor）→ 1001 点 Z11(dB)
```

新代码：`backend/app/ml/surrogate/`（baseline / dataset / model / infer，纯函数模块）+ 上述脚本；确定性位置规则作为新函数加入 `app/core/sparse_sampler.py`（提取与推理共用）。遵循现有 `backend/app/ml/sparse/` 模式；魔数进 `AlgorithmConfig`。

## 5. 训练数据提取

### 5.1 提取流程

`extract_surrogate_dataset.py`（离线脚本，直接调用服务层函数，不走 HTTP；DB URL 与 DATA_ROOT 参数化，默认桌面版路径 `~/.aln-data/`）：

**复用 aln-data2 已有构件**（`app/services/nn_export.py` + `app/core/sparse_sampler.py`）：

- 查询：`build_nn_export_stmt`（`devices ⋈ batches`，标量列齐全：eg/fl/ag、7 性能参数、s_param_path/port、batch_no），filters 指定 `batch_no='#2'` + `eg/fl/area_um2` 非空
- 读谱：`read_z_mag_db(path, port, target_n_freq=1001)` —— 按 `s_param_port` 取 S11/S22 端口转 |Z| dB 并插值，S1P/S2P 通吃
- 行迭代参考：`iter_nn_export_rows` 的流式模式

**新增逻辑**（nn_export 未覆盖的）：

1. §5.2 数据清洗（nn_export 不做离群过滤）
2. §5.3 确定性稀疏位置规则（替代 nn_export 的内容依赖随机采样，见下）
3. §5.4 基准表、§5.5 网格单元划分
4. 落地为 `dataset.npz`（float32，约 400MB）+ `meta.json`（归一化统计、基准表、划分清单）—— 不用 nn-csv 宽表（38,400 × 2617 列 CSV 体积与解析开销大，npz 更适合训练）

### 5.2 数据清洗（新增）

按网格单元稳健过滤，剔除提取离群器件：

- `|fs − 单元中位数| > 100 MHz` 或 `|fp − 单元中位数| > 100 MHz`（阈值可调，进 AlgorithmConfig）
- 物理非法：`qs ≤ 0`、`qp ≤ 0`、`fp ≤ fs`、标签缺失
- 预计剔除 5–15%

### 5.3 确定性稀疏位置规则

位置只依赖 fs/fp 与频率轴（**不看频谱内容**，保证训练/推理一致），K=256：

| 区域 | 范围 | 配额 |
|---|---|---|
| 谐振密集区 | `[fs−0.5Δ, fp+0.5Δ]`，Δ=fp−fs | 60% |
| 过渡带 | 两侧各 1Δ | 25% |
| 远带 | 其余频段 | 15% |

各区内在频率轴格点上均匀取点；fs 贴近频段边缘时截断并把配额让给相邻区域。

**与现有 `fixed_rule_sample` 的关系**（为什么不能直接复用）：

- `fixed_rule_sample` 的区域划分 `partition_regions(z_db, freq)` **依赖真值频谱内容**（主模/杂模/平滑分区），而推理时 Stage 1 没有真值谱，位置规则必须只由 fs/fp 决定
- 其主模区内用 `np.random.choice` **随机加权**采样，同一器件两次提取点不同；训练（真值谱随机采样）与推理（预测 fs/fp 确定性位置）分布不一致，会给 Stage 2 引入额外偏移
- 因此新增 `deterministic_sample_positions(freq_axis, fs, fp, k)` 放进 `app/core/sparse_sampler.py`，提取管线与推理链路共用同一实现；`fixed_rule_sample` 保留给现有 nn-csv 导出与旧 reconstructor 流程

### 5.4 基准表

对 12 个 `(eg=0, fl=0, area)` 单元，清洗后计算：

- `params_base(area)`：7 维性能参数均值
- `z_base_full(area)`：1001 点均值谱

随 `meta.json` 保存，并作为 buffer 存进 Stage 1 模型。推理时面积不在 12 档上：相邻面积档 dB 域线性插值。

### 5.5 数据集划分

- 12 个基准单元**固定进训练集**（推理要查基准）
- 其余 468 格按单元划分 train/val/test = 372/48/48
- test 格训练时完全不见，真实检验"预测未流片设计"

## 6. Stage 1：基准锚定残差 MLP

```
输入 (eg, fl, area)  z-score，3 维（预留 AG 第 4 维：恒 0 + mask）
  → Linear 256 → GELU → 残差块 ×2（Linear 256 → GELU → Linear 256）
  → head_params：Δparams（7 维，z-score 目标）
  → head_sparse：Δsparse_z（256 维，dB 按 meta 统计归一）
输出 = 基准(area) + 残差
```

- 残差语义：`Δparams = params − params_base(area)`；`Δsparse_z = z_device(pos) − interp(z_base_full(area), pos)`，pos 由各器件自身 fs/fp 按 §5.3 生成
- 损失：`L = MSE(params_norm) + λ·MSE(sparse_z)`，λ 默认 0.1 可调
- 训练：AdamW lr=3e-4 + cosine，batch 512，≤300 epochs，val 网格早停，MPS
- train/infer 一致性：训练用真值 fs/fp 算采样位置，推理用预测 fs/fp；若评估显示位置敏感，加"预测位置重采样微调"收尾步骤（可选改进项）

## 7. Stage 2：复用 sparse reconstructor + 微调

接口已核实（`backend/app/ml/sparse/reconstructor.py`）：`cond(5) + 变长稀疏点(padding mask) + spline 基线 → 全谱 = 基线 + 残差×scale`。K 与位置分布无关，**零结构改动**。

- cond：取 Stage 1 预测 7 维中的 `[fs, fp, Qs, Qp, kt2]`；两个 bodeQ 仅作 Stage 1 输出展示，不进 Stage 2
- 现有 checkpoint：`checkpoints/sparse/308nm/`（target_k=300，结构兼容）
- 三步走：
  1. **零样本诊断**：308nm checkpoint 直接在我们的真值稀疏点上评估，判断其训练频段是否匹配批次 #2
  2. **Step A**：真值稀疏点微调（适配 K=256 与确定性位置分布）—— 微调数据同样用 §5.3 的确定性位置规则生成（而非 nn_export 的随机采样），保证与推理输入分布一致
  3. **Step B**：输入换成 Stage 1 预测点继续微调（Stage 1 冻结），消除级联分布偏移
- 若第 1 步分布差异太大：从头训练 Stage 2（复用 `train_sparse_reconstructor.py` 流程换数据集）

## 8. 评估方案与成功标准

三层指标（全部在未见过网格单元的 test 集上报告，另按主模区/杂模区/远带分区报告）：

| 层 | 指标 | 目标 |
|---|---|---|
| 性能参数 | fs / fp MAE | ≤ 10 / 15 MHz |
| 稀疏点 | 256 点 z RMSE | ≤ 2 dB |
| 全谱（端到端） | Stage 2 输出 RMSE；预测谱重提取 fs MAE | **RMSE ≤ 0.5 dB**；重提取 fs MAE ≤ 15 MHz |

> 0.5 dB 为收紧后的目标；若主模区达标而远带不达标，按区域分别评估结论。
> 口径说明：全谱 RMSE 由远带平滑区主导（1001 点中约 800 点变化平缓），稀疏点误差集中在谐振区（60% 点在主模区），故稀疏点层目标（≤2 dB）宽于全谱层（≤0.5 dB）并不矛盾。

对照基线：

- **B0 纯基准**：同面积基准谱直接当预测（残差=0）；要求全谱 RMSE 比 B0 降 ≥ 50%
- **B2 网格插值**：相邻 EG/FL 格均值谱线性插值（传统 DOE 查表法）

`evaluate_surrogate.py` 输出：JSON 报告 + 预测 vs 真值谱对比图（每 test 格抽代表器件）+ 误差分布直方图 + RMSE 随 (EG, FL) 热图。

## 9. 工程与测试

- 环境：uv + `backend/.venv`，PyTorch + MPS；入口 `uv run python scripts/...`
- 配置：采样配额、清洗阈值、K=256 等魔数进 `AlgorithmConfig`；训练超参走脚本 argparse
- 测试（`backend/tests/ml/`，pytest）：
  - 位置规则：确定性、配额、fs 贴近频段边缘的截断
  - 基准表：查表/面积插值正确性；零残差输出恒等于基准
  - 提取管线：2 个构造的小 s2p + 内存 sqlite 端到端夹具测试
  - 模型 forward 形状 + Stage 2 接口契约（mock reconstructor）
- 实施前置：按 CLAUDE.md 路由，ML 工作在 worktree `/Users/jingbozuo/Projects/aln-data-backend-ml`（`feat/backend-ml`）进行；该 worktree 当前不存在，实施第一步创建

## 10. 风险与开放点

| 风险 | 应对 |
|---|---|
| 308nm checkpoint 训练频段与批次 #2 不匹配 | Step 0 零样本诊断；不匹配则从头训 Stage 2 |
| 全谱 0.5 dB 目标过紧 | 分区域报告；先保主模区，远带视结论再优化 |
| 预测 fs/fp 误差导致采样位置偏移 | 评估敏感性；必要时"预测位置重采样微调" |
| 测试格上残差学习不优于插值基线 B2 | 评估直接对比 B0/B2，用数据决定是否迭代架构 |
| AG 后续批次有值 | 输入第 4 维 + mask 已预留，重新提取数据即可 |
