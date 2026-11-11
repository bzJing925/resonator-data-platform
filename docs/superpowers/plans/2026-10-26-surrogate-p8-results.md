# P8：频率特征契约实验结果

2026-10-26。结论：**按冻结判据保留 B0（几何契约），B1 频率条件模型未达标**——训练内主指标改善 −15.5%（阈值 −20%）且约束指标恶化 +14.5%（阈值 5%）。但回顾性外层上 B1 把 8 个 700-PF 结构的 Qp MAE 从 118.761 降到 75.165、joint_scaled 覆盖 0/8→6/8（伴随区间显著变宽）。此为已见数据上的描述性对照，不构成选择依据；B1 适用场景为"结构已有实测频率"，不与几何契约混比。

依据：[P8 冻结协议](2026-10-26-surrogate-p8-protocol.md)（运行前保存，含先验检查）。工作目录 `/Users/jingbozuo/Projects/aln-data-backend-ml/backend`；产物 `surrogate_data/optimization_p8_20261026/`。

## 臂与契约

- B0：P6C trend_gp_log_area（几何三特征，`contract=geometry_only`），复用 P6C/P6D 保存预测，rtol=1e-12 校验一致。
- B1：同一冻结配方结构/核/缩放，特征扩为 [EG, FL, log area, fs_ghz, fp_ghz]（`contract=measured_frequency`）。k² 在此契约下近似 fs/fp 的确定性函数，其改善属退化性质，不当作模型能力证据。

## 训练内验证（选择依据）

主指标（qp × PF EG0 分支 × area_low 伪边界单元）与约束指标（balanced 分支×目标等权）：

| 臂 | 主指标 | 相对 B0 | 约束指标 | 相对 B0 |
|---|---:|---:|---:|---:|
| B0_control | 0.264410 | — | 0.028233 | — |
| B1_freq_trend_gp | 0.223530 | −15.5% | 0.032311 | +14.5% |

**B1 未达标，eligible 为空，保留 B0。** 逐单元看，B1 的改善集中在 700 单元（structure_holdout fl0 0.299→0.042、fl1 0.347→0.192；eg/fl_upper 同样改善），但 area_edges 的 900 单元显著恶化（fl0 0.033→0.356）。频率特征在真实失败位点（700）训练内有效，但代价是边界内层其他水平。

## 外层回顾性结果（已见数据，不作选择依据）

外层 test MAE（joint_scaled；k² 单位百分点）：

| scope | 臂 | k² | Qs | Qp | BodeQ |
|---|---|---:|---:|---:|---:|
| structure_holdout | B0 | 0.0640 | 14.804 | 29.217 | 21.284 |
| | B1 | 0.0623 | 16.787 | 30.105 | 23.817 |
| area_edges | B0 | 0.1543 | 20.479 | 41.786 | 27.252 |
| | B1 | 0.1306 | 20.077 | 36.032 | 29.158 |
| eg_upper | B0 | 0.1934 | 19.410 | 85.721 | 83.323 |
| | B1 | 0.1236 | 23.083 | 74.403 | 79.843 |
| fl_upper | B0 | 0.1038 | 16.978 | 34.142 | 38.253 |
| | B1 | 0.1006 | 17.782 | 39.959 | 42.146 |

小面积 PF Qp（8 个 700 μm² 结构，area_edges）：

| 臂 | MAE | joint_global 覆盖 | joint_scaled 覆盖 |
|---|---:|---:|---:|
| B0_control | 118.761 | 0/8 | 0/8 |
| B1_freq_trend_gp | 75.165 | 4/8 | 6/8 |

B1 未命中的是 FL=0.5（实际 512.9，区间 [232.8, 455.8]）与 FL=0.75（423.4，[252.3, 418.5]）。注意覆盖改善同时来自更好的点预测和**显著更宽的区间**（如 FL=1.75 宽度 123.7→295.3，+139%）；area_edges 全体联合覆盖（joint_scaled）B0 0.583→B1 0.750，structure_holdout 0.941→0.882，fl_upper 0.944→0.889。协议先验（fp 在 700 处落在分支面积趋势上、Qp 逆势上行）被部分推翻：频率在固定几何水平内携带了 Qp 变异信息（训练内 700 单元大幅改善），但先验关于趋势段的部分成立（900 内层单元恶化）。

## 判断

1. 协议判据未被满足：频率契约臂不提供全面更优的模型，**P3/P6C 几何契约模型与产品维持不变**。B1 的全部数字保留为对照。
2. B1 的结果仅限"已有实测频率"场景；它不能用于未测结构寻优，不改变 `trusted_for_unmeasured_optimization=false`。
3. 700-PF Qp 的可预测性边界因本轮收窄：部分水平差信息藏在实测频率中（且可被模型利用），但按当前冻结阈值不足以换模；900 内层单元恶化提示该信息在边界内部并不一致。
4. 区间变宽贡献了一部分覆盖改善；不能把 6/8 全部归功于点模型（对照 P6C 的区间归一化讨论）。

## 遗留决策项

1. 是否值得为频率契约单独立一轮新冻结协议（如 fs−fp 分离度特征、仅 Qp 的频率条件子模型），须先冻结新的训练内验证方案；按已见 test 继续改 B1 不构成新盲测。
2. 是否将"实测频率条件评估"作为独立的交付模式（服务于来料复检而非寻优）——产品方向决策。
3. 新尺寸外推仍需独立实测；本轮无新尺寸建议。

## 复现与验证

新增源码：`app/ml/optimization_freq_p8.py`、`scripts/run_optimization_p8.py`、`scripts/verify_optimization_p8.py`、`tests/ml/test_optimization_p8.py`。产物：`frozen_inputs.json`、`selection.json`（先于外层写入）、`inner_predictions.csv`、`area_stress_predictions.csv`、`primary_cells.csv`、`guard_cells.csv`、`predictions.csv`（含 `contract` 列）、`metrics.csv`、`joint_coverage.csv`、`interval_failures.csv`、`small_area_pf_qp.csv`、`calibration.json`、`models.joblib`、`verification.json`。

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-ml/backend
uv run --no-sync python scripts/run_optimization_p8.py \
  --p3-root surrogate_data/optimization_p3_20261015 \
  --p6c-root surrogate_data/optimization_p6c_20261022 \
  --p6d-root surrogate_data/optimization_p6d_20261022 \
  --protocol /Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/docs/superpowers/plans/2026-10-26-surrogate-p8-protocol.md \
  --out surrogate_data/p8_reproduce_new
uv run --no-sync python scripts/verify_optimization_p8.py \
  --root surrogate_data/p8_reproduce_new \
  --p6c-root surrogate_data/optimization_p6c_20261022 \
  --p6d-root surrogate_data/optimization_p6d_20261022 \
  --out surrogate_data/p8_reproduce_new/verification.json
uv run --no-sync ruff check app/ml/optimization_freq_p8.py scripts/run_optimization_p8.py scripts/verify_optimization_p8.py tests/ml/test_optimization_p8.py
```

验证：Ruff 通过；P8 新增 4 项测试通过；独立核验 10/10（源码不变、B0 与 P6C/P6D 文件 rtol=1e-12、折组隔离、选择复算一致、选择先于外层、可信标志全 false、契约标注逐臂匹配、B0 外层逐条一致、8 结构子集完整、产物清单完整）。输入清单哈希运行前后不变；未修改产品算法、标签、既有 dirty 文件；无 Git 提交、无部署。
