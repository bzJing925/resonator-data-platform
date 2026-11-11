# P6C：训练内选择的趋势/面积表示消融

2026-10-22。P6C 已完成，实验模型没有替换 P3/P4/P5，没有部署。训练内验证选择的配方在多个区域改善平均误差，但 **EG 边界仍差于 P3，小面积 PF 的 Qp 仍全部漏覆盖**，不满足可信外推要求。

[冻结协议](2026-10-22-surrogate-p6c-protocol.md)定义全部配方、划分、选择指标与校准方式。[P6B](2026-10-22-surrogate-p6b-results.md)的诊断用于提出这些配方，因此即使本轮选择只用训练内数据，外层结果仍是已见数据上的回顾性评估，不是新盲测。

工作目录：`/Users/jingbozuo/Projects/aln-data-backend-ml/backend`。产物：`surrogate_data/optimization_p6c_20261022/experiment/`。全部输入为原 P3 去嵌结构表；没有仿真标签、实测伪标签、raw/de 混合或物理硬单调约束。没有猜 AG/层厚，没有重新划分外层数据。

## 完成的消融

四种配方：原 P6A GP；仅 log 面积；原面积上的 Ridge 趋势加 GP 残差；log 面积上的同样趋势加 GP 残差。GP 核参数固定；每个分支、每目标独立拟合。趋势在 log 目标上拟合，GP 拟合其残差。GP 方差不含趋势参数不确定性，不视为完整后验。

每个外层拟合集内部，以 design_group 为单位分成四折。仅通过内部预测的“每结构四目标平均绝对 log 误差→组内平均→全部组平均”选择一个配方；四项目标共用该配方。校准/test 标签都不能影响这一步。指标对所有设计组等权，**没有保证少数分支或外推边界最坏误差**。

四种外层划分均选中 `trend_gp_log_area`：

| 外层划分 | 原 GP 内部误差 | 所选配方内部误差 |
|---|---:|---:|
| 普通结构 | 0.030883 | 0.026397 |
| 面积两端 | 0.028854 | 0.025422 |
| EG 上边界 | 0.031658 | 0.026728 |
| FL 上边界 | 0.030453 | 0.025622 |

这些是绝对 log 误差，不是 Q 的绝对误差或直接百分比。全部内部预测、折分配、四配方分数均保存，没有按外层 test 选目标/分支赢家。原 GP 控制配方的点预测与归一化联合区间复现 P6A（rtol=1e-12）。

## 外层回顾性误差

所选配方的 MAE；括号为相对 P3 Extra Trees 的比例，小于 1 为改善。k² 单位为百分点。

| 划分 | k² | Qs | Qp | Bode Q |
|---|---:|---:|---:|---:|
| 普通结构 | 0.0640（0.482） | 14.804（0.784） | 29.217（0.753） | 21.284（0.658） |
| 面积两端 | 0.1543（0.380） | 20.479（0.533） | 41.786（0.739） | 27.252（0.931） |
| FL 上边界 | 0.1038（0.377） | 16.978（0.679） | 34.142（0.534） | 38.253（0.532） |
| EG 上边界 | 0.1934（2.127） | 19.410（1.132） | 85.721（2.073） | 83.323（2.421） |

普通、面积、FL 的四指标平均误差均改善。EG 比 P6A 固定 GP 有改善，但四指标仍劣于 P3，不能整体换模。

小面积 PF Qp 的 8 个结构，所选配方 MAE 为 **118.761**，比原 GP 的 108.742 更大；两种联合校准下均 **0/8 命中**。全部配方的这一子集结果保存在 `small_area_pf_qp.csv`。总体面积 MAE 下降掩盖了少数分支退步，训练内平均分不能作为边界验收指标。

## 区间校准与宽度代价

分别做全局绝对 log 残差和 GP 标准差归一化残差的四目标联合设计组校准；每种配方都保留两种区间。点预测在两种区间之间完全相同。覆盖定义为同一设计组中全部被评估结构、全部四目标共同命中。

| 划分 | 所选配方：全局联合 | 所选配方：归一化联合 | P6A GP 归一化联合 | 归一化区间宽度/P3 范围 |
|---|---:|---:|---:|---:|
| 普通，34 组 | 28/34（82.4%） | 32/34（94.1%） | 28/34（82.4%） | 0.86–1.63 |
| 面积，48 组 | 20/48（41.7%） | 28/48（58.3%） | 23/48（47.9%） | 0.74–1.80 |
| FL，36 组 | 34/36（94.4%） | 34/36（94.4%） | 36/36（100%） | 0.85–2.30 |
| EG，48 组 | 21/48（43.8%） | 48/48（100%） | 48/48（100%） | 1.72–4.04 |

宽度比例为四指标各自中位区间宽度与 P3 的比值范围。不能把覆盖改善全部归功于点模型，也不能把 EG 的 100% 覆盖当成准确预测。面积 700 μm² 的归一化联合区间，Qp 结构覆盖为 75%；其中 PF 仍 0/8。原协议的名义 90% 不自动成为边界覆盖保证，普通留出的 94.1% 也不是新尺寸实测验收。

## 决定与后续交接

1. 保留 P3/P4/P5，不升级可信支持范围；本轮全部预测 `trusted_for_unmeasured_optimization=false`。保留四配方及两个区间方案，不能仅展示所选配方的好区域。
2. 停止根据当前外层 test 继续微调这四种配方。若进入下一轮，应先冻结新的训练内验证设计，明确考察少数分支及训练范围内的伪边界，而不是事后挑 test 赢家。
3. 小面积 PF 需要单独处理标签不确定性/重复样本与分支偏差。当前仅用 P3 结构中位数，未完成层级测量误差建模；不把位置 MAD 直接当噪声方差，也不将观测 CI 重叠重新计作预测覆盖。
4. 仿真仍独立用于规律诊断。P6B 的 k² 反例和选峰切换尚不支持硬单调约束，COMSOL 缺电学损耗尤其不能补 Q 标签。
5. 下一轮复杂算法可由 Astra 接手；先评估是否有足够实测设计组支撑分支/边界选择和校准。真正新尺寸外推仍需冻结预测和独立实测；制造范围、真实 AG/层厚与工程容差未确认前，不生成可下单新尺寸。

## 复现与验收

主要文件：`selection.json`、四组 `*_inner_folds.csv` / `*_inner_predictions.csv`、`predictions.csv`、`metrics.csv`、`comparison_to_p3.csv`、`joint_coverage.csv`、`interval_failures.csv`、`small_area_pf_qp.csv`、`calibration.json`、`fit_receipts.json`、`models.joblib`。

验证：41 项回归测试通过，Ruff 和两个 worktree 的 diff 检查通过。独立重算四组内部选择分数、32 个联合校准分位数，核对 10624 条外层预测及保存模型。外层标签扰动不会改变内部选择。117 个既有 dirty 文件，以及 P3/P4/P5/P6A/P6B 的 71/92/39/74/56 个产物保持不变。

运行目录为 ML worktree 的 backend，输出必须使用未存在的新目录：

```bash
uv run --no-sync python scripts/run_optimization_p6c.py --p3-root surrogate_data/optimization_p3_20261015 --p6a-root surrogate_data/optimization_p6a_20261022 --protocol /Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/docs/superpowers/plans/2026-10-22-surrogate-p6c-protocol.md --out surrogate_data/p6c_reproduce_new/experiment
uv run --no-sync python scripts/verify_optimization_p6c.py --root surrogate_data/optimization_p6c_20261022
uv run --no-sync pytest -q tests/ml/test_optimization_p3.py tests/ml/test_optimization_p4.py tests/ml/test_optimization_p6a.py tests/ml/test_optimization_p6b.py tests/ml/test_optimization_p6c.py
```

实验可复现不等于可信外推。本轮未提交 Git、未运行 COMSOL 求解、未改变产品模型。

完整重跑位于 `reproduction/experiment/`，17 个 CSV/选择/校准/划分收据文件字节一致，见 `reproduction_check.json`；不声称 joblib 二进制字节一致。
