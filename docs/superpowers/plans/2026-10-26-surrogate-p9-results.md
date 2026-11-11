# P9：层级标签不确定性模型实验结果

2026-10-26。结论：**按冻结判据保留 C0**；C1（异方差 GP）把训练内目标子集 Qp 出折区间覆盖从 65.4% 提到 96.2%、外层 8 结构覆盖 0/8→6/8，但 balanced 点预测恶化 +72%，越过 5% 约束；C2（异方差校准）覆盖无增益。位置 MAD 离散度代理确实能改善目标子集的区间行为，但代价是整体点精度，按协议不换模。

依据：[P9 冻结协议](2026-10-26-surrogate-p9-protocol.md)（运行前保存）。工作目录 `/Users/jingbozuo/Projects/aln-data-backend-ml/backend`；产物 `surrogate_data/optimization_p9_20261026/`。

## 臂与冻结定义

离散度代理 `disp = {target}_position_mad / {target}`（逐结构逐目标）。C0 控制（trend_gp_log_area + P6C 双校准，重拟合并与 P6C 内部/外层逐点校验 rtol=1e-12）；C1 把 GP 的 WhiteKernel(0.05) 换成逐点 `alpha_i = 1e-10 + (disp_i/s)²`（s=分支拟合折 log 残差标准差）；C2 点模型与 C0 逐位相同，仅校准尺度改为含 disp 的组合尺度。P6B/P6C 未授权"MAD 当噪声方差"，本轮经用户批准以实验臂显式建模；MAD 不是中位数方差，仅作冻结代理，不作物理噪声解释。

内层区间评估为新冻结机制：四折组隔离 CV 出折预测 + 逐目标 leave-one-group-out 共形 90% 分位数，全部使用训练角色数据。

## 训练内验证（选择依据）

目标子集 = 各拟合集 area_low 水平的 PF EG0 结构（26 个 (scope, structure) 单元）。scaled 变体：

| 臂 | 子集 Qp 覆盖 | balanced 覆盖 | 子集中位半宽 | 点主指标（stress） | 点约束（balanced） |
|---|---:|---:|---:|---:|---:|
| C0_control | 65.4% | 98.9% | 0.171 | 0.264410 | 0.028233 |
| C1_hetero_gp | **96.2%** | 98.8% | 0.240（1.40×） | 0.214506（−18.9%） | **0.048692（+72%）** |
| C2_hetero_calibration | 61.5% | 97.3% | 0.207（1.21×） | = C0 | = C0 |

冻结判据：子集覆盖增益 ≥+25pp 且宽度 ≤2× 且 balanced 覆盖降 ≤5pp；C1 另须点指标恶化 ≤5%。C1 满足全部区间判据（+30.8pp、1.40×、−0.1pp）但**点约束严重越限**（+72%）；C2 覆盖无增益（−3.8pp）。eligible 为空，**保留 C0**。

C1 的异方差噪声等价于对高离散结构加大平滑：区间在目标子集显著更准，但整体点预测变钝。协议的点/区间双约束正是为防止"用点精度换覆盖"而设，规则按设计生效。

## 外层回顾性结果（已见数据，不作选择依据）

小面积 PF Qp（8 个 700 μm² 结构，area_edges）：

| 臂 | MAE | joint_global 覆盖 | joint_scaled 覆盖 | 中位 log 半宽（scaled） |
|---|---:|---:|---:|---:|
| C0_control | 118.761 | 0/8 | 0/8 | 0.283 |
| C1_hetero_gp | 87.686 | 6/8 | 6/8 | 0.589（2.1×） |
| C2_hetero_calibration | 118.761 | 1/8 | 1/8 | 0.273 |

C1 的 6/8 同时来自点移动与约 2 倍宽的区间；其 area_edges 整体 Qp MAE 恶化（41.8→68.2）。联合覆盖（scaled）：area_edges C0 0.583→C1 0.729 / C2 0.667；eg_upper 1.000→1.000 / 0.271；fl_upper 0.944→0.917 / 0.694；structure_holdout 0.941→0.882 / 0.971。全部臂的逐条记录保存在产物中。

## 判断

1. 层级标签不确定性建模**能**改善目标子集区间（训练内 65.4%→96.2%，外层 0/8→6/8），证明标签离散度携带了当前区间缺失的信息——这是 P9 立项问题的肯定回答。
2. 但按冻结的点/区间双判据，C1 的整体点精度代价不可接受，C2（仅校准）增益不足；**产品和冻结对照链不变**。若未来要采用异方差区间，需要先由用户裁决"区间优先 vs 点精度优先"的验收权衡，并用新的冻结协议定义可接受的点精度损失。
3. 与 P7/P8 合看：700-PF Qp 失败的剩余解释空间进一步收窄——几何特征面（P6C/P6D）、趋势外推（P7 A1）、分支偏差（P7 A3）、频率契约（P8 B1）、标签不确定性（P9 C1/C2）均不能在冻结判据下换模；P8/P9 各自在目标子集上有回顾性改善但都伴随明确的别处代价。
4. 所有输出 `trusted_for_unmeasured_optimization=false`；新尺寸外推仍需独立实测，本轮无新尺寸建议。

## 遗留决策项

1. **区间 vs 点精度权衡**：是否接受以 balanced 点误差换目标子集覆盖（如接受，需新协议冻结可容忍的损失上限与适用场景）。
2. 是否把异方差区间作为"仅报告"模式附加到现有页面（不改变排名，只改区间显示）——产品决策。
3. 离散度代理的物理核实：位置 MAD 的测量学含义（工艺 vs 仪器）未核实；若立项需设计复核。
4. P8 遗留项（频率契约的精细化、来料复检模式）继续有效。

## 复现与验证

新增源码：`app/ml/optimization_label_p9.py`、`scripts/run_optimization_p9.py`、`scripts/verify_optimization_p9.py`、`tests/ml/test_optimization_p9.py`。产物：`frozen_inputs.json`、`selection.json`（先于外层写入）、`inner_interval_predictions.csv`、`area_stress_predictions.csv`、`inner_interval_metrics.csv`、`point_primary_cells.csv`、`point_guard_cells.csv`、`predictions.csv`、`metrics.csv`、`joint_coverage.csv`、`interval_failures.csv`、`small_area_pf_qp.csv`、`calibration.json`、`models.joblib`、`verification.json`、`reproduction_check.json`。

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-ml/backend
uv run --no-sync python scripts/run_optimization_p9.py \
  --p3-root surrogate_data/optimization_p3_20261015 \
  --p6c-root surrogate_data/optimization_p6c_20261022 \
  --p6d-root surrogate_data/optimization_p6d_20261022 \
  --protocol /Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/docs/superpowers/plans/2026-10-26-surrogate-p9-protocol.md \
  --out surrogate_data/p9_reproduce_new
uv run --no-sync python scripts/verify_optimization_p9.py \
  --root surrogate_data/p9_reproduce_new \
  --p6c-root surrogate_data/optimization_p6c_20261022 \
  --p6d-root surrogate_data/optimization_p6d_20261022 \
  --out surrogate_data/p9_reproduce_new/verification.json
uv run --no-sync ruff check app/ml/optimization_label_p9.py scripts/run_optimization_p9.py scripts/verify_optimization_p9.py tests/ml/test_optimization_p9.py
```

验证：Ruff 通过；P9 新增 7 项测试通过，P3–P9 共 61 项 ML 回归测试通过；独立核验 9/9（源码不变、C0 内部/外层与 P6C 文件 rtol=1e-12 一致、折组隔离、指标与选择复算一致、选择先于外层、可信标志全 false、8 结构子集完整、产物清单完整）；完整重跑全部 CSV/JSON 字节一致（models.joblib 二进制跨进程不同，沿用惯例）。输入清单哈希运行前后不变；未修改产品算法、标签、既有 dirty 文件；无 Git 提交、无部署。
