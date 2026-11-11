# P10：子集异方差区间 + 仅 Qp 频率子模型实验结果

2026-10-26。结论：**两个子实验按冻结判据均保留控制臂**——E1 的保守臂 D1（点不动、仅子集换异方差区间）训练内子集覆盖 52.6%→94.7%（达标 +25pp），但子集宽度 4.5× 超过 4× 上限；D2（子集切 C1）覆盖增益 +21pp 未达 +25pp。E2 的仅 Qp 频率子模型主指标改善 −15.5%（未达 −20%）。最终推荐配置维持 P6C 冻结行为；供页面"仅报告"显示决策的权衡证据已打包（见判断节）。

依据：[P10 冻结协议](2026-10-26-surrogate-p10-protocol.md)（运行前保存）。两个子实验共享冻结输入与纪律但机制独立，合并为一轮以减少协议开销；二者选择互不影响。工作目录 `/Users/jingbozuo/Projects/aln-data-backend-ml/backend`；产物 `surrogate_data/optimization_p10_20261026/`（`experiment1_intervals/`、`experiment2_qp_freq/`）。

## 实验一：子集异方差区间

子集 = PF EG0 分支且 area=700 的结构（纯几何规则，内层 19 个评估单元；area_edges 拟合集无 700 水平，内层该 scope 无子集单元）。臂：D0 逐位复用 P9 C0；D1 点不动、仅子集行区间尺度换 sqrt(std²+disp²)（沿用 C0 分位数）；D2 子集行整体换 P9 C1（点+区间+其分位数）。内层为 P9 冻结机制（出折预测 + 逐目标 LOO 共形 90%），全部由 P9 保存预测装配，无新拟合。

| 臂 | 子集 Qp 覆盖 | 覆盖增益 | 子集中位半宽 | 宽度比 | balanced 覆盖 | 非子集逐位一致 |
|---|---:|---:|---:|---:|---:|---|
| D0_control | 52.6% | — | 0.114 | 1× | 95.8% | — |
| D1_subset_hetero_interval | 94.7% | +42.1pp ✓ | 0.512 | **4.5× ✗** | 96.3% ✓ | ✓ |
| D2_subset_c1_switch | 73.7% | **+21.1pp ✗** | 0.201 | 1.8× ✓ | 95.6% ✓ | ✓ |

判据：增益 ≥+25pp、宽度 ≤4×、非子集逐位一致、balanced 降 ≤5pp；D2 另须点守卫 ≤5%（通过：主指标 0.2145 vs 0.2644 改善、balanced 0.0487 vs 0.0282 恶化 +72% ✗——此守卫沿用 P9 的 C1 点精度结果，D2 子集点即 C1 点）。D1 卡在宽度，D2 卡在覆盖增益与点守卫，**按规则选 D0**。

外层回顾（已见数据）：D1 joint_scaled 在 8 结构上 8/8 覆盖但中位 log 半宽 1.592（D0 的 5.6×）；D2 为 6/8、宽度 2.1×、MAE 87.7（子集点换 C1 所致）。joint_global 下 D1 仅 0/8（disp 相对绝对 log 尺度太小）。全体联合覆盖（scaled）：area_edges D0 0.583→D1 0.750 / D2 0.708，其余 scope 基本不变。

## 实验二：仅 Qp 频率子模型

F1：仅 Qp 用 P8 B1 式五特征 [EG, FL, log area, fs, fp]（`contract="measured_frequency"`），其余三目标几何契约（`contract="geometry_only"`）。内层由 P8/P6C/P6D 保存预测装配，无新拟合。

| 臂 | 主指标（stress area_low PF qp） | 相对 | 约束（balanced） | 相对 |
|---|---:|---:|---:|---:|
| F0_control | 0.264410 | — | 0.028233 | — |
| F1_qp_frequency | 0.223530 | **−15.5%（未达 −20%）** | 0.029516 | +4.5%（≤5% 通过） |

**保留 F0**。约束从 P8 的 +14.5% 降到 +4.5%——证实 P8 的约束恶化来自 Qs/BodeQ 被频率契约拖累，仅 Qp 子模型按用户预期消除了这部分；但主指标不变（主指标本来就是纯 Qp），仍不达标。

协议要求的 900 内层单元专项检查：area_edges 拟合集 area_low=900 处恶化依旧存在——fl0 单元 0.0327→0.3562（约 11×）、fl1 单元 0.1276→0.1408；而 700 单元（structure_holdout fl0 0.2988→0.0421、fl1 0.3467→0.1917）大幅改善。频率特征对 Qp 的帮助集中在 700 失败位点，在 900 处反而有害，符号不一致是主指标无法达标的原因。

外层回顾：F1 在 8 结构上 MAE 118.8→75.2、覆盖 4/8（global）/ 5/8（scaled）（略低于 P8 B1 的 6/8——混合校准分位数不同），宽度 1.7×；全体联合覆盖 area_edges 0.583→0.708，fl_upper 保持 0.944（修复了 P8 B1 的 0.889），其余 scope 不变。

## 判断

1. 两个子实验的冻结判据均未满足，**冻结对照链与产品行为完全不变**；全部臂的内外层数字保留为对照。
2. E1 暴露了保守臂的真实代价结构：点不动时子集覆盖只能靠宽度换（4.5×–5.6×），D2 的 1.8× 宽度换 +21pp 仍差一点——判据按设计拒绝了"靠加宽换覆盖"。
3. E2 证明频率契约对 Qp 的约束指标影响可控，但 900 单元的反向恶化是客观的；主指标未达标。
4. 供页面"仅报告"显示的推荐配置：**维持 C0/P6C 区间**（本轮选择结果）。若用户愿意为子集放宽宽度上限或覆盖阈值，可选菜单已量化：D1（8/8，宽 4.5–5.6×）、D2（6/8，宽 1.8–2.1×、子集点换 C1）、F1（5/8、宽 1.7×、仅 Qp 换频率契约）。这些都是回顾性证据，选择任何一条都需要新协议或用户明确裁决。

## 遗留问题

1. 页面"仅报告"显示实施（用户已同意方向，待其按本结果选定区间方案后另行实施；本轮未动产品）。
2. 是否放宽 E1 宽度上限（4×）或覆盖阈值（25pp）——任何放宽都是新协议，不能追溯适用本轮。
3. 900 单元恶化的机理未定位（频率特征在低面积水平的负作用），列为后续诊断候选。
4. 新尺寸外推仍需独立实测；本轮无新尺寸建议。

## 复现与验证

新增源码：`app/ml/optimization_p10.py`、`scripts/run_optimization_p10.py`、`scripts/verify_optimization_p10.py`、`tests/ml/test_optimization_p10.py`。产物：`frozen_inputs.json`（P3/P6C/P6D/P8/P9 全部清单哈希 + 源码哈希）、`selection_e1.json` / `selection_e2.json`（均先于各自外层写入）、两个子目录的完整预测/指标/覆盖/失败明细/小面积表，根目录 `summary.json`、`outputs_manifest.json`、`verification.json`、`reproduction_check.json`。

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-ml/backend
uv run --no-sync python scripts/run_optimization_p10.py \
  --p3-root surrogate_data/optimization_p3_20261015 \
  --p6c-root surrogate_data/optimization_p6c_20261022 \
  --p6d-root surrogate_data/optimization_p6d_20261022 \
  --p8-root surrogate_data/optimization_p8_20261026 \
  --p9-root surrogate_data/optimization_p9_20261026 \
  --protocol /Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/docs/superpowers/plans/2026-10-26-surrogate-p10-protocol.md \
  --out surrogate_data/p10_reproduce_new
uv run --no-sync python scripts/verify_optimization_p10.py \
  --root surrogate_data/p10_reproduce_new \
  --p3-root surrogate_data/optimization_p3_20261015 \
  --p6c-root surrogate_data/optimization_p6c_20261022 \
  --p9-root surrogate_data/optimization_p9_20261026 \
  --out surrogate_data/p10_reproduce_new/verification.json
uv run --no-sync ruff check app/ml/optimization_p10.py scripts/run_optimization_p10.py scripts/verify_optimization_p10.py tests/ml/test_optimization_p10.py
```

验证：Ruff 通过；tests/ml 全套 **104 项通过**（含 P10 新增 5 项）；独立核验 **13/13**（源码不变、E1 指标与选择复算一致、E1 外层非子集逐位一致、D0 与 P9 C0 逐条一致、F0 与 P6C 逐条一致、契约标注逐目标正确、选择文件先于外层、可信标志全 false、产物清单完整）；完整重跑全部 CSV/JSON 字节一致（models.joblib 二进制跨进程不同，沿用惯例）。输入清单哈希运行前后不变；未修改产品算法、标签、既有 dirty 文件；无 Git 提交、无部署。
