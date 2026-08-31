# 谐振器代理模型（Surrogate Model）设计规格

日期：2026-07-20（v2：同步 aln-data2 后修订 §5，提取流程复用 `nn_export` / `sparse_sampler` 构件；v3：参考 `/Users/jingbozuo/Documents/kimi/workspace/BAW_光谱ML分析报告.md` 修订——清洗升级两级、评估加 B3 轻量基线、相位通道预留、标签循环性风险；v4：输入改 5 维（+PF massloading、AG 默认 1μm）、每结构组合≤5 条抽样、物理器件绑定划分）
状态：已评审（头脑风暴阶段确认）

## 1. 背景与目标

基于已上传的谐振器测试数据，训练一个**代理模型**：输入器件结构参数，预测性能参数与完整阻抗谱 Z11(dB)，用于"预测未流片设计"的离线分析。

- **输入（v4，5 维）**：EG、FL、AG（当前默认 1.0 μm，数据全空时的占位常量，预留真实数据）、面积（area_um2）、PF（massloading 有无，0/1，取自 `mapping_entries.has_pf`）
- **输出**：7 个性能参数（fs、fp、kt2、Qs、Qp、qs_bodeq、qp_bodeq）+ 1001 点 Z11(dB) 全谱
- **交付形态**：纯离线（训练脚本 + 评估报告 + CLI 推理），不改 Web 平台；精度验证达标后再决定是否做 UI 集成

## 2. 已确认的关键决策

| 决策点 | 结论 |
|---|---|
| 输入维度 | **5 维 (EG, FL, AG, 面积, PF)**；AG 当前恒 1.0 占位，PF ∈ {0,1}（v4） |
| 训练集抽样 | **每个结构参数组合（eg,fl,area,pf）取 ≤5 条**，按 \|fs−格中位数\| 升序选取（v4） |
| 建模范围 | 只用批次 `#2`（4–7 GHz），不跨频段 |
| 交付形态 | 先离线验证再定 UI 集成 |
| 架构 | 方案 A：两阶段级联（Stage 1 代理模型 + Stage 2 稀疏重建） |
| Stage 1 结构 | 基准锚定残差：eg=0/fl=0 同 PF 单元为基准，网络只学残差 |
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
- 基准组（eg=0, fl=0）：1,200 器件（A1-* PF=0 与 G1-* PF=1 各 600）；格间 fs 均值仅 4.65–4.85 GHz，证实"曲线形状相似、频点偏移小"
- **离群值坑**：个体器件 fs 散布 4.0–6.15 GHz（Fail 器件提取抓到杂波/谐波），必须清洗
- **PF（massloading）数据源（v4）**：`mapping_entries.has_pf` 布尔列（195 条 has_pf=1，mark 形如 H1-1，描述含"EG0+PF"字样）；经 `devices.mark ⋈ mapping_entries.mark`（同 mapping_id）关联。批次 #2：PF=0 36,300 条、PF=1 5,400 条（仅覆盖 8 个 eg|fl 组合）、**mark 为空无法关联的 1,200 条剔除**（PF 未知）
- **端口-面积配对（v4）**：同一 .s2p 文件产生 S11/S22 两行，面积不同（mapping 的 `area_s11`/`area_s22` 互补配对，如 700↔5500、2500↔3000），合占 12 个面积档。划分时**同一文件的两行必须绑定同侧**（否则同谐振器的 fs/fp 经另一端口泄漏）
- **含 PF 的结构组合**：576 个（PF=0 满网格 40×12=480 + PF=1 的 8×12=96）

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

- 查询：`build_nn_export_stmt`（`devices ⋈ batches`，标量列齐全：eg/fl/ag、7 性能参数、s_param_path/port、batch_no），filters 指定 `batch_no='#2'` + `eg/fl/area_um2` 非空；**v4 追加 ⋈ `mapping_entries`（同 mapping_id 的 mark 关联）取 `has_pf`**；mark 为空/关联失败的记录剔除（约 1,200 条）
- 读谱：`read_z_mag_db(path, port, target_n_freq=1001)` —— 按 `s_param_port` 取 S11/S22 端口转 |Z| dB 并插值，S1P/S2P 通吃
- 行迭代参考：`iter_nn_export_rows` 的流式模式

**新增逻辑**（nn_export 未覆盖的）：

1. §5.2 数据清洗（nn_export 不做离群过滤）
2. §5.3 确定性稀疏位置规则（替代 nn_export 的内容依赖随机采样，见下）
3. §5.4 基准表、§5.5 网格单元划分
4. **每格抽样（v4）**：清洗后，每个结构组合 (eg, fl, area, pf) 按 |fs−格中位数| 升序取前 5 条进训练/val/test 集（`surr_per_cell_max=5`）；全集规模 ≈576 格 × 5 ≈ **2,900 条**。基准表构建不受此限（用全部合法基准格记录）
5. 落地为 `dataset.npz`（float32，抽样后仅 ~15MB）+ `meta.json`（归一化统计、基准表、划分清单、剔除清单）—— 不用 nn-csv 宽表（npz 更适合训练）
6. **相位通道预留**：读谱时同时保存 `phase(Z)` 通道到 npz（训练暂不使用）——分析报告 §5.2(a) 指出双通道信息对谐振/Q 敏感任务价值显著（类比 2DUV 交叉峰），为后续双通道模型留数据

### 5.2 数据清洗（新增，两级）

借鉴分析报告 §5.2(c) 的"公式即数据清洗器"思路（SISSO 公式在小样本含噪数据上以近 100% 精度识别误标样本）：

1. **单元内稳健过滤**：`|fs − 单元中位数| > 100 MHz` 或 `|fp − 单元中位数| > 100 MHz`（阈值可调，进 AlgorithmConfig）
2. **跨单元趋势一致性**：固定面积档下，fs/fp 随 (eg, fl) 应平滑变化；对每个面积档的单元中位数拟合 fs(eg,fl)、fp(eg,fl) 二维趋势面（先线性，残差结构明显时升级局部多项式），剔除偏离趋势面 > 3σ 的器件——抓"单元内正常、跨格趋势异常"的漏网离群
3. **物理非法**：`qs ≤ 0`、`qp ≤ 0`、`fp ≤ fs`、标签缺失
4. 预计剔除 5–15%；剔除清单记入 `meta.json` 便于回溯

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

### 5.4 基准表（v4：加 PF 维）

对 `(eg=0, fl=0, pf, area)` 基准单元（A1-* 为 pf=0、G1-* 为 pf=1，各覆盖全部 12 个面积档，共 24 个基准档），用**全部合法基准格记录**（不受每格 ≤5 抽样限制）计算：

- `params_base(pf, area)`：7 维性能参数均值
- `z_base_full(pf, area)`：1001 点均值谱

随 `meta.json` 保存，并作为 buffer 存进 Stage 1 模型。查询 `lookup(area, pf)`：面积不在 12 档上时相邻面积档 dB 域线性插值；pf 精确匹配（不插值）。

### 5.5 数据集划分（v4：物理器件绑定）

- 同一 .s2p 文件的 S11/S22 两行**绑定同侧**——划分单元是"物理器件组"（eg, fl, pf, mark），共约 288 组（576 格 ÷ 2 端口）
- 24 个基准档对应器件组**固定进训练集**（推理要查基准）
- 其余器件组划分 train/val/test ≈ 8:1:1（`surr_val_groups`/`surr_test_groups` 进 AlgorithmConfig）
- test 组训练时完全不见，真实检验"预测未流片设计"

## 6. Stage 1：基准锚定残差 MLP

```
输入 (eg, fl, ag, area, pf)  z-score，5 维（ag 当前恒 1.0 占位；pf ∈ {0,1}）
  → Linear 256 → GELU → 残差块 ×2（Linear 256 → GELU → Linear 256）
  → head_params：Δparams（7 维，z-score 目标）
  → head_sparse：Δsparse_z（256 维，dB 按 meta 统计归一）
输出 = 基准(area, pf) + 残差
```

- 残差语义：`Δparams = params − params_base(pf, area)`；`Δsparse_z = z_device(pos) − interp(z_base_full(pf, area), pos)`，pos 由各器件自身 fs/fp 按 §5.3 生成
- 损失：`L = MSE(params_norm) + λ·MSE(sparse_z)`，λ 默认 0.1 可调
- 训练：AdamW lr=3e-4 + cosine，batch 512，≤300 epochs，val 网格早停，MPS
- train/infer 一致性：训练用真值 fs/fp 算采样位置，推理用预测 fs/fp；若评估显示位置敏感，加"预测位置重采样微调"收尾步骤（可选改进项）

## 7. Stage 2：复用 sparse reconstructor + 微调

接口已核实（`backend/app/ml/sparse/reconstructor.py`）：`cond(5) + 变长稀疏点(padding mask) + spline 基线 → 全谱 = 基线 + 残差×scale`。K 与位置分布无关，**零结构改动**。

- cond：取 Stage 1 预测 7 维中的 `[fs, fp, Qs, Qp, kt2]`；两个 bodeQ 仅作 Stage 1 输出展示，不进 Stage 2
- **checkpoint 状态（v3 更新）**：原 `checkpoints/sparse/308nm/` 已在 aln-data2 覆盖同步时按用户指示删除（untracked，不可恢复）。**Stage 2 主路径改为：在我们的提取数据上从头训练**（复用 `train_sparse_reconstructor.py` 流程，采样换成 §5.3 确定性规则，K=256）；若后续从 aln-data2 仓库或训练机找回 308nm checkpoint，可再评估微调加速
- 两步训练：
  1. **Step A**：真值稀疏点训练/微调（K=256 确定性位置分布）—— 微调数据同样用 §5.3 的确定性位置规则生成（而非 nn_export 的随机采样），保证与推理输入分布一致
  2. **Step B**：输入换成 Stage 1 预测点继续微调（Stage 1 冻结），消除级联分布偏移

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
- **B3 轻量模型**（分析报告切入点①）：结构参数→性能参数的 ETR/XGBoost 回归（分钟级训练、白盒可解释）；**Stage 1 的 params 头必须显著优于 B3**——报告指出训练集 <500 样本时公式/轻量模型反超 NN，我们每格仅约 80 样本，这条基线是 NN 方案的必要性检验

`evaluate_surrogate.py` 输出：JSON 报告 + 预测 vs 真值谱对比图（每 test 格抽代表器件）+ 误差分布直方图 + RMSE 随 (EG, FL) 热图。

### 8.1 实测结果（2026-08-10，批次 #2 真实数据）

数据：38,400 条 JOIN 成功 → 清洗保留 23,614（cell_outlier 14,572 / trend_outlier 214，晶圆内离散为主）→ 每格 ≤5 抽样 **2,840 条 / 384 物理组（train 326 / val 29 / test 29）**，12 面积档 × 2 PF。

**关键发现——逐器件指标存在物理下限**：同格（同结构参数）器件因晶圆工艺离散，fs 留一 MAE 5.2 MHz、全谱格内不可约 RMSE **0.97 dB（train）/ 0.79 dB（test）**。模型输入在格内恒定 → **逐器件全谱 RMSE ≤ 0.5 dB 不可达**；公平口径应为格均值参照或位移对齐指标。

| 指标 | 目标 | 实测 | 判定 |
|---|---|---|---|
| fs MAE | ≤ 10 MHz | 11.41 MHz（B3: 9.46；地板 5.2） | ✗（B3 更优） |
| fp MAE | ≤ 15 MHz | 14.90 MHz（B3: 17.27） | ✓ |
| 全谱 RMSE（逐器件） | ≤ 0.5 dB | **1.20 dB**（dense 2.65 / transition 0.60 / far 0.39） | ✗（地板 0.79） |
| 全谱 RMSE（格均值参照） | — | **0.93 dB**（B0: 1.68 / B2: 0.99） | 新基准口径 |
| 全谱 RMSE（±45MHz 位移对齐） | — | **0.72 dB** | 形状已低于地板 |
| 重提取 fs MAE | ≤ 15 MHz | 10.04 MHz | ✓ |
| vs B0 提升 | ≥ 50% | 36.2% | ✗ |
| vs B2（逐器件/格均值） | 优于 | 1.20 vs 1.28 / 0.93 vs 0.99 | ✓ 均优于 |

结论：级联**形状重建达标**（对齐口径 0.72 dB ≤ 地板），残差几乎全部是谐振**位置**误差（fs MAE 11.4 MHz vs 地板 5.2 MHz）。Stage 2 训练中发现并修复两个结构性缺陷（见下"训练修复记录"）。pf=1 分组指标：rmse_full 1.08 dB、fs MAE 6.8 MHz（优于 pf=0 的 1.21/11.9，48 组样本量小仅供参考）。

**训练修复记录**（任务 14 迭代）：

1. Stage 1 欠拟合：bs512→64（优化步数 5→38/epoch），lr 3e-4→1e-3；fs 17.9→11.4 MHz。λ_sparse 0.02 实验变差（0.1 最佳）；多次重训取 val 最优（种子噪声 ±2 MHz）。
2. **SparseReconstructor 频率标尺缺陷**：`max_freq=10` 使 4–7 GHz 归一化到 0.4–0.7（跨窗 0.3 rad），正弦位置编码近似常数、注意力均匀化（熵 5.52/5.54），残差通路学到 ≈0（Step A 等价样条插值）。修复：max_freq=0.1（窗口映射 40–70，q_pos 跨窗 std 0.0075→0.235）。
3. **cond/z 未归一化**：Qs/Qp 数百量级 + dB 直接入 LayerNorm 压垮位置信号；改为 params/z 统计归一化（O(1) 激活）。
4. Step B（规格 §7 预案）已执行：用 Stage 1 预测点微调 Stage 2（lr 5e-3、from-scratch、早停 epoch 168，val 0.0390 归一化 MSE），全谱 1.30→1.20 dB 并反超 B2。

**后续迭代方向**（按杠杆率排序）：① fs/fp 精度是唯一的硬瓶颈——B3 已证 9.5 MHz 可达，Stage 1 参数头可借鉴树模型特征或直接用 B3 输出替换（偏离原架构，需决策）；② 若有晶圆坐标等逐器件特征入模，可突破格内地板；③ 评估主口径建议改为格均值参照（目标可定为 ≤0.5 dB，当前 0.93）。

### 8.2 COMSOL 仿真补充实验（2026-08-19，负结果）

动机：D1/D2 COMSOL 仿真（1339 SNP，4.2–8 GHz）能否作为实测补充，填充未覆盖的插值区域。仿真 100% 覆盖实测 568 格，另有 183 个中间水平新格（L_AG=1.0 对齐后 63 格可用）。域差 Δfs 中位 +57.7 MHz 且强面积相关（corr −0.76）→ ExtraTrees Δ 校准器（仅用纯 train 格训练防泄漏，按 eg|fl 分组 CV）残差 fs 9.9 / fp 13.9 MHz；仿真 Q 系统性偏高 +485% 不可用（训练侧 pmask 屏蔽）。混合训练：Stage 1 实测 2,391 条 + 63 校准伪标签格（权重 0.3，仅 fs/fp/kt2 有效）。

多种子消融（3 种子 mean ± std，MHz；scripts/hybrid_ablation.py）：

| 配置 | test fs | test fp | 留出 20 仿真格 fs | 留出 20 仿真格 fp |
|---|---|---|---|---|
| baseline（纯实测） | 13.80 ± 2.78 | 18.48 ± 3.32 | 11.42 ± 2.08 | 13.50 ± 2.87 |
| hybrid63（全量仿真） | 13.74 ± 1.37 | 17.62 ± 0.97 | 11.94 ± 2.75（组内参考） | 12.58 ± 1.92（组内参考） |
| hybrid43（留 20 格） | 14.28 ± 1.05 | 18.42 ± 1.37 | 14.19 ± 0.95 | 13.69 ± 1.14 |

**结论：负结果，不更换生产 checkpoint**（保留纯实测 stage1.pt）。

1. 缝隙填充无收益：公平对比 baseline vs hybrid43（均未见留出 20 格），仿真格 fs MAE 11.42→14.19 反而变差。63 格 × 0.3 权重相对 2,391 条实测信号太弱，且伪标签噪声（≈10 MHz）已接近模型自身误差，教不出新东西。
2. 对实测精度无害也无益：三组 test 差异全在种子噪声内（实测种子彩票达 ±3.4 MHz，比此前估计 ±2 更大；单次对比无意义，必须多种子）。
3. 有价值的独立验证：纯实测模型在缝隙格上对校准伪标签 fs MAE 11.4 MHz ≈ 校准器 CV 噪声 9.9 MHz——实测网格本身够密，"未覆盖"区域的模型插值已与仿真物理一致，COMSOL 数据独立确认了 Stage 1 缝隙行为的可信度。

基础设施保留（仿真格增多或校准改善后可复用）：`scripts/sim_offset_analysis.py`（域差分析）、`build_sim_supplement.py`（校准伪标签生成）、`train_surrogate_stage1.py --sim-npz/--sim-weight/--seed`、`hybrid_ablation.py`（多种子消融框架）。报告：`backend/sim_offset_report/`、`backend/ckpt_ablation/results.json`（均 gitignored）。

### 8.3 仿真参考清洗 + Zp 校正（2026-08-19，Zp 峰高 MAE 3.24→0.62 dB）

动机：Zp 峰（fp 处反谐振峰高）拟合差——test 逐器件峰高 MAE 3.24 dB（bias −2.34 系统性欠预测），小面积（700/1200/1500）atfp MAE 5–9 dB、max 18.4 dB。用户要求峰高口径逼近 0.5 dB。

诊断链（scripts/zp_peak_diagnosis.py 及分解实验）：

1. 晶圆离散使"每格按 |fs−中位数| 选器件"的目标峰高互相平均；基准表（B0 锚）由全部 basic 器件均值构建，峰被压低 7–13 dB——欠预测结构性来源。
2. 误差分解：真值稀疏点样条重建峰 MAE 仅 0.11 dB（稀疏表征+Stage 2 架构无损）；瓶颈在 Stage 1 稀疏头的顶点（MAE 3.54 dB）；λ_sparse/顶点/峰区加权只能压到 3.35 dB 饱和。
3. 结构→Zp 含模态交叉不连续（DOE 文档亦指出 Q/杂散属树模型领域）：同一训练集上 ExtraTrees (eg,fl,area,pf)→Zp 达 MAE 0.62 dB（格内地板 0.30 dB）→ 混合方案。

措施：

- **仿真参考清洗**（用户处方）：每格按校准仿真距离（MAD 归一 |Δfs|+|Δfp|+|ΔZp|）选 ≤5 器件；Δ 校准器纯 train 格拟合防泄漏（覆盖 555/568 格）；基准表改用仿真选中的 basic 器件构建。效果：选中器件格内 Zp std 1.58→0.47 dB，基准谱峰 +7~+13 dB，Stage 1 best val 0.117→0.070。
- **Zp 校正器**：ExtraTrees 结构→Zp（scripts/train_zp_corrector.py），推理时对级联谱峰区施加 σ=12MHz 窄高斯加性校正使顶点匹配树预测（infer.py 自动探测 zp_corrector.joblib，平台/CLI/探索器透明生效）。
- **Step B 工程修复**：1001 查询点注意力在 MPS 上实测比 CPU 慢 7 倍（35.8 vs 5.3 s/step）→ `--device cpu`；训练态目标频率点子采样（查询点相互独立故 MSE 无偏，~4×）→ Step B 从 2.5h+ 降至 ~35 min；`--w-peak`/`--w-apex` 加权保留为可选（被校正器取代）。

结果（test；新数据集为仿真选中器件，同分组同划分）：

| 指标 | 旧（中位数清洗） | 新（仿真清洗+校正） |
|---|---|---|
| Zp 峰高 MAE / bias | 3.24 dB / −2.34 | **0.62 dB / +0.05** |
| 小面积 700/1200/1500 峰高 MAE | 5.05 / 4.60 / 8.98 | **0.78 / 0.61 / 0.45** |
| Zp atfp MAE | 3.59 | 0.80（残留 max 22.8 dB 为单格 fp 差 78 MHz 的频率问题，非峰高） |
| fs / fp MAE | 11.41 / 14.90 MHz | 11.05 / 12.43 MHz（B3 同数据重训：11.15 / 15.03） |
| rmse_full / cellmean / aligned | 1.20 / 0.93 / 0.72 dB | 1.20 / 0.93 / 0.73 dB（校正代价 +0.02~0.04） |

全谱指标持平、Zp 峰高 5 倍改善。逐器件"最大误差 ≤0.5 dB"物理不可达（格内 std 0.30 dB 意味着器件间 max 必然超 0.5；个别格 fp 误差 >50 MHz 时峰沿 dB 误差不可避免）——当前达到 MAE 0.62 / p95 1.54 / 小面积 ≤0.78。后续可选迭代：Stage 1 λ_sparse=0.5 变体 fs/fp 更优（10.39/11.53 MHz），采纳需重跑 Step B。

生产 stack 已切换：`surrogate_data/`（仿真清洗版）+ `checkpoints_surrogate/`（含 zp_corrector.joblib；B3 已在新数据重训）；旧版保留在 `surrogate_data_prev/`、`checkpoints_surrogate_prev/`。

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
| ~~308nm checkpoint 训练频段与批次 #2 不匹配~~ （v3：checkpoint 已删除） | Stage 2 从头训练为主路径；找回旧 checkpoint 后再评估微调加速 |
| 全谱 0.5 dB 目标过紧 | 分区域报告；先保主模区，远带视结论再优化 |
| 预测 fs/fp 误差导致采样位置偏移 | 评估敏感性；必要时"预测位置重采样微调" |
| 测试格上残差学习不优于插值基线 B2 / 轻量基线 B3 | 评估直接对比 B0/B2/B3，用数据决定是否迭代架构 |
| **PF=1 只覆盖 8 个 eg\|fl 组合**（v4）：pf=1 的泛化范围受限，test 含 pf=1 格时结论需分开报告 | 评估按 pf 分组报告指标；pf=1 组合不足时只对已有组合做插值结论 |
| **每格仅 5 条样本**（v4）：合计 ~2,900 条，NN 对轻量模型的优势窗口变窄 | B3 基线是硬性对照（规格 §8）；必要时加数据增广或回到全量记录 |
| **标签循环性**（分析报告 §5.3）：fs/fp/Q 标签来自传统提取算法，模型只会复现其系统误差 | §8 闭环重提取验证 + 人工抽检高置信样本；长期以高置信仿真标签破局 |
| AG 后续批次有真实值 | 输入第 3 维已占位（当前恒 1.0），重新提取数据即可 |

**开放点（本版不实现）**：

- **MBVD 物理基线**（分析报告路线 (f)，其主推方向）：ML 预测 MBVD 元件值＋电路方程物理组谱，外推与跨体系迁移更强；但项目明确不实现 mBVD 6 列、且网格内插值下经验基准含杂散模均值信息更准。若未来外推到新频段/新叠层，切换该路线
- **仿真谱预训练→实测微调**（分析报告切入点②）：当前实测数据充足（38,400 条）不需要；新频段数据稀缺时启用，含域随机化增广
- **双通道（幅值＋相位）模型**：相位通道已在 §5.1 预留存储，v2 模型升级方向
- **SISSO 显式公式**（分析报告切入点①完整版）：B3 用 ETR/XGBoost 起步；若需要白盒公式与"20 样本标定新机"，再引入 SISSO
