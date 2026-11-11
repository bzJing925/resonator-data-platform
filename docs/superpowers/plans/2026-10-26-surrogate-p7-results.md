# P7：小面积 PF Qp 偏差建模受控实验结果

2026-10-26。结论：**三个新实验臂均未达到协议冻结的成功判据，按规则保留 A0 控制（P6C 所选 trend_gp_log_area）；外层小面积 PF Qp 仍为 0/8 联合覆盖（A1 仅把 MAE 从 118.761 降到 110.200、joint_scaled 覆盖 1/8），且 A1 的面积两端整体误差与联合覆盖显著恶化。** 本轮为回顾性实验，无新盲测；P3/P4/P5/P6 系列模型与产物全部保留，未部署任何新模型。

依据：[P7 冻结协议](2026-10-26-surrogate-p7-protocol.md)（运行前保存）。工作目录 `/Users/jingbozuo/Projects/aln-data-backend-ml/backend`；产物 `surrogate_data/optimization_p7_20261026/`。

## 实验臂与训练内验证

四臂（全部沿用冻结 GP/Ridge/四配方/划分/校准秩）：A0 控制；A1 趋势分量 log area 截断到分支拟合范围（GP 残差不变）；A2 逐 (scope, target) 配方选择；A3 分支×面积平均 log 残差按 n/(n+4) 收缩偏移（拟合范围外为 0）。

选择完全使用训练内证据：P6C 四折组隔离 CV（A1/A3 新算 128 个内部拟合；折分配与 P6C 逐行一致）+ P6D 机制的面积轴伪边界（area_low/area_high，A1/A3 新算）。A0/A2 复用 P6C/P6D 保存预测并按 rtol=1e-12 校验一致。

冻结主指标（qp × PF EG0 分支 × area_low 伪边界单元，设计组平均后单元等权）与约束指标（P6D balanced 分支×目标等权）：

| 臂 | 主指标（越小越好） | 相对 A0 | 约束指标 | 相对 A0 |
|---|---:|---:|---:|---:|
| A0_control | 0.264410 | — | 0.028233 | — |
| A1_trend_area_clip | 0.246553 | −6.8% | 0.029679 | +5.1% |
| A2_per_target_recipe | 0.264410 | 0 | 0.028453 | +0.8% |
| A3_branch_area_offset | 0.264410 | 0 | 0.027615 | −2.2% |

判据（主指标改善 ≥20% 且约束恶化 ≤5%）：无臂达标（eligible 为空），**按冻结规则保留 A0**。逐单元看，A1 在 8 个主指标单元中改善 6 个（−0.022…−0.035 log），但 structure_holdout 的 pf1_eg0_fl0 单元恶化 +0.032；净改善不足且约束指标越限。

A2 的 Qp 在所有四个 scope 的内部 CV 中仍选 trend_gp_log_area（逐目标选择对 Qp 无任何改变；仅 bodeq_fitted 在 eg_upper/fl_upper 改选 gp_log_area）。A3 在 area_low 单元上偏移恒为 0（面积水平未在训练折内出现），与 A0 逐位相同——协议预期的负面对照行为成立。

## 外层回顾性结果（已见数据，不作选择依据）

外层 test 四目标 MAE（joint_scaled 列，k² 单位为百分点）：

| scope | 臂 | k² | Qs | Qp | BodeQ |
|---|---|---:|---:|---:|---:|
| structure_holdout | A0 | 0.0640 | 14.804 | 29.217 | 21.284 |
| | A1 | 0.0640 | 14.804 | 29.217 | 21.284 |
| | A2 | 0.0640 | 14.804 | 29.217 | 21.284 |
| | A3 | 0.0586 | 14.566 | 27.581 | 21.027 |
| area_edges | A0 | 0.1543 | 20.479 | 41.786 | 27.252 |
| | A1 | **0.3872** | **31.401** | **51.903** | **33.172** |
| | A2 | 0.1543 | 20.479 | 41.786 | 27.252 |
| | A3 | 0.1543 | 20.479 | 41.786 | 27.252 |
| eg_upper | A0 | 0.1934 | 19.410 | 85.721 | 83.323 |
| | A2 | 0.1934 | 19.410 | 85.721 | **129.674** |
| | A3 | 0.1903 | 19.139 | 85.731 | 82.760 |
| fl_upper | A0 | 0.1038 | 16.978 | 34.142 | 38.253 |
| | A2 | 0.1038 | 16.978 | 34.142 | **52.532** |
| | A3 | 0.1031 | 16.827 | 33.698 | 38.710 |

（A1 在 eg_upper/fl_upper 与 A0 相同：测试面积均在拟合范围内，截断不触发；A3 同理在 area_edges 与 A0 相同。）

小面积 PF Qp（8 个 700 μm² 结构，area_edges）：

| 臂 | MAE | joint_global 覆盖 | joint_scaled 覆盖 |
|---|---:|---:|---:|
| A0_control | 118.761 | 0/8 | 0/8 |
| A1_trend_area_clip | 110.200 | 0/8 | 1/8 |
| A2_per_target_recipe | 118.761 | 0/8 | 0/8 |
| A3_branch_area_offset | 118.761 | 0/8 | 0/8 |

A1 仅把 8 结构预测抬高约 2–3%（如 FL=2.0：501.1→512.6，实际 600.1），唯一新命中为 FL=1.75（joint_scaled 区间 [404.3, 530.9]，实际 525.1）。代价是 area_edges 整体四目标全部恶化且 joint_scaled 设计组共同覆盖从 28/48 跌到 5/48——截断趋势在 5500 端与其余边缘结构造成的损失远大于 700-PF 子集的收益。A2 的 bodeq 改选在 eg_upper 上 MAE 恶化 55.6%。A3 在随机留出中把 Qp MAE 略降（29.2→27.6），证明分支×面积偏差在样本内部分可学习，但在未观测面积水平上结构性无效。

## D1 位置构成诊断

PF EG0 两分支 96 个结构：标签在"同分支同面积 ≥80% 结构共有坐标子集"上的 log 偏移 |Δ| 中位 0.0072、p90 0.1031、最大 0.3675；8 个目标结构中位 0.0240、最大 0.2494（共有坐标 10–12/12–19）。而 A0 在这 8 个结构上的绝对 log 残差中位 0.256（0.148–0.676）。标签构成敏感性比模型残差小约一个数量级——**坐标构成不能解释外层低估**，与 P6H 的位置匹配结论一致。

## 判断

1. 本轮三个有界假设均被否决：边界平坦趋势（A1）训练内改善不足且以外层整体恶化为代价；逐目标配方选择（A2）对 Qp 无操作空间；分支×面积偏差（A3）样本内可学但不向未观测面积外推。全部臂的内外层数字保留作失败对照。
2. 训练内代理的保真度得到确认：structure_holdout/fl_upper/eg_upper 拟合集内的 area_low=700 单元误差（0.30–0.35 log）与外层 700 失败量级（中位 0.256 log）一致，后续轮次可在训练内迭代而不必依赖外层 test。
3. 失败机制收窄为：700 μm² PF 的 Qp **水平**高于 [EG, FL, area] 特征面的拟合值，而非趋势斜率、配方粒度、可收缩偏差或标签构成。由于面积两端留出在结构上排除 700，任何仅从训练数据学习的特征面都无法预知该水平差——这指向需要新信息（新特征/物理先验/层级标签模型），而这些均是留给用户的方向性决策。
4. 不改变 P3–P6 任何结论与产品；所有输出 `trusted_for_unmeasured_optimization=false`。

## 需要用户决策的事项

1. **特征契约**：是否允许把 fs/fp（实测派生，未测结构未知）作为输入特征，或改做频率条件模型——改变"未测结构可预测"的契约。
2. **层级标签模型**：是否立项 P8，把坐标构成/位置离散显式建进标签不确定性（P6C 交接项，本轮 D1 已量化其上限）。
3. **划分解冻**：是否解冻 P3 划分重做（会破坏全部既有对照链，本轮明确不做）。
4. **仿真先验**：仍受 P6A 约束（COMSOL 无电学损耗），不引入。
5. 新尺寸外推结论仍需独立实测；本轮没有任何新尺寸建议。

## 复现与验证

新增源码： `app/ml/optimization_bias_p7.py`、`scripts/run_optimization_p7.py`、`scripts/verify_optimization_p7.py`、`tests/ml/test_optimization_p7.py`。产物：`frozen_inputs.json`（P3/P6C/P6D 全部文件 SHA256 + 源码哈希）、`selection.json`（先于外层预测写入）、`inner_predictions.csv`（25,664 行）、`area_stress_predictions.csv`（4,416 行）、`primary_cells.csv`、`guard_cells.csv`、`predictions.csv`（10,624 条外层）、`metrics.csv`、`joint_coverage.csv`、`interval_failures.csv`、`small_area_pf_qp.csv`、`d1_coordinate_composition.csv`、`calibration.json`、`models.joblib`、`verification.json`、`reproduction_check.json`。

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-ml/backend
uv run --no-sync python scripts/run_optimization_p7.py \
  --p3-root surrogate_data/optimization_p3_20261015 \
  --p6c-root surrogate_data/optimization_p6c_20261022 \
  --p6d-root surrogate_data/optimization_p6d_20261022 \
  --protocol /Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/docs/superpowers/plans/2026-10-26-surrogate-p7-protocol.md \
  --out surrogate_data/p7_reproduce_new
uv run --no-sync python scripts/verify_optimization_p7.py \
  --root surrogate_data/p7_reproduce_new \
  --p6c-root surrogate_data/optimization_p6c_20261022 \
  --p6d-root surrogate_data/optimization_p6d_20261022 \
  --out surrogate_data/p7_reproduce_new/verification.json
uv run --no-sync ruff check app/ml/optimization_bias_p7.py scripts/run_optimization_p7.py scripts/verify_optimization_p7.py tests/ml/test_optimization_p7.py
uv run --no-sync pytest -q tests/ml/test_optimization_p3.py tests/ml/test_optimization_p4.py tests/ml/test_optimization_p6a.py tests/ml/test_optimization_p6b.py tests/ml/test_optimization_p6c.py tests/ml/test_optimization_p6d.py tests/ml/test_optimization_p7.py
```

验证：Ruff 通过；P3–P7 共 50 项 ML 回归测试通过；独立核验 12/12（源码不变、A0 与 P6C/P6D 文件 rtol=1e-12 一致、折组隔离、选择分数复算一致、selection.json 先于 predictions.csv、可信标志全 false、A0/A2 外层与 P6C 逐条一致、8 结构子集完整、产物清单完整）；完整重跑全部 CSV/JSON 字节一致（models.joblib 二进制跨进程不同，沿用 P6A/P6C 惯例不声称模型字节一致）。输入清单哈希运行前后不变；未修改产品算法、标签、既有 dirty 文件；无 Git 提交、无部署。
