# P6B：小面积 Qp 与仿真峰选择诊断结果

日期：2026-10-22。已完成回顾性诊断，**没有训练或替换模型，没有把 COMSOL 标签/规律写入实测模型**。P3/P4/P5/P6A 保留。

入口：[P6B 协议](2026-10-22-surrogate-p6b-protocol.md)、[P6A 结果](2026-10-22-surrogate-p6a-results.md)。ML 根目录 `/Users/jingbozuo/Projects/aln-data-backend-ml/backend`；本轮产物 `surrogate_data/optimization_p6b_20261022/`。应使用 **`diagnostics_v2/`**，`diagnostics/` 为保留的初版诊断，不作为最终分辨率结论。

## 结论与下一步选择

当前证据不支持给模型加入“EG 增大则 k² 必定增大”的硬约束，也不支持把 GP 区间当成边界可靠性保证。下一轮优先研究实测数据的分支偏差、聚合标签不确定性、几何缩放与 GP 趋势项；复杂模型与仿真先验都必须先做受控消融。

本轮没有新测量，现有 test 均已查看；任何后续实验继续标为回顾性。既有四目标优化页继续使用冻结 P3/P4 数据，不将多个指标的不同结构峰值拼在一起。

## 1. 小面积 PF 的 Qp 低估与标签不确定性同时存在

P6A 的面积边界 GP 联合校准，700 μm² 共 48 个结构，命中 30 个；18 个漏覆盖中，10 个无 PF、8 个有 PF。PF 的 8 个结构全部低估，误差范围 −204.55 至 −72.02，平均 −108.74。

| 小面积分组 | 数量 | Qp 区间命中 | 位置 MAD/Qp 中位数 | 预测落在观测中位数 CI 内 |
|---|---:|---:|---:|---:|
| 无 PF，漏覆盖 | 10 | 0/10 | 52.45% | 5/10 |
| 无 PF，命中 | 30 | 30/30 | 32.48% | 21/30 |
| 有 PF | 8 | 0/8 | 15.58% | 7/8 |

观测 CI 是 P3 结构表的 `qp_ci_low/high`，不等同于预测区间；落在 CI 内不能改写此前覆盖率。PF 的相对 MAD 并非最大，不能将全部失败归因于测量噪声；但 7/8 预测落在较宽的观测中位数 CI 内，说明把聚合标签当成精确值也过于简单。此处只观察到关联，尚未证明损耗机理或制造原因。

校准与边界残差不同：联合阈值 2.310；小面积 PF 的标准化绝对 log 残差为 5.296–12.008。PF 校准仅 8 个独立设计组（无 FL 分支 1 组，有 FL 分支 7 组），不足以对这两个分支分别给出有限的 90% 设计组分位数。不能事后按本次 test 放大阈值，再将新覆盖率称作独立验证。

具体结构保留在 `small_area_qp.csv`：G1-1 至 G8-1，面积均 700 μm²、PF=1、EG=0，FL 从 0 到 2 μm。其中 G2-1 的观测 Qp=512.923，预测 308.378，联合区间 [279.624, 340.088]；观测 CI 为 [290.582, 560.281]。G7-1 是 PF 组中预测未进入观测 CI 的一个结构：观测 525.099，预测 446.652，观测 CI [481.688, 530.227]。

此外，小面积点与最近训练点的最大信号核相似度中位数为 0.992；PF 的 log 标准差中位数仅 0.0310（校准 0.0281）。几何上“近”没有揭示这个分支的系统偏差，因此距离、凸包和 GP 标准差都不能单独成为可信支持规则。

## 2. EG 边界 GP 与训练均值的关系

EG 边界折只训练 EG≤1.5，测试 EG=2。与训练点的最大信号核相似度中位数从校准点的 0.965 降至测试点的 0.352。以下是自然对数尺度相对训练均值的绝对距离中位数：

| EG=2 测试目标 | GP 预测到训练均值 | 观测值到训练均值 |
|---|---:|---:|
| k² | 0.0289 | 0.0797 |
| Qs | 0.0634 | 0.1516 |
| Qp | 0.0930 | 0.2518 |
| Bode Q | 0.0605 | 0.2011 |

这些数值与预测向训练均值收缩的解释一致，帮助定位 P6A 固定核的失败；尚未通过改变单一模型因素的消融证明根因。没有据此直接调 length scale 或替换模型。后续若试验趋势项、各维尺度或面积变换，应由训练内的设计组划分选择，保持 calibration/test 不参与选择。

## 3. 仿真 EG 趋势对提峰口径敏感

对全部 98 条可审计 EG 宽度序列执行相同检查，涉及 390 个独立 s1p、292 个相邻尺寸对。没有只挑反例分析。每个原始 fs/fp 都与 P6A 的提取值重新对应。

对选中的同一个峰，用三个邻近频点的 log|Z| 做抛物线插值；不重新选峰。频段并非均匀网格：峰外最大步长 2 MHz，fs/fp 邻域步长均约 0.2 MHz。按 fs/fp 各自 ±半个局部步长传播到指标，得到**分辨率敏感性包络**。该包络不是统计置信区间，也不是 COMSOL 物理/网格误差上界。

| 指标 | 原始方向 | 同峰插值后方向 |
|---|---|---|
| fp，96 条原始递增 | 96 条递增 | 90 条递增，6 条轻微非单调 |
| fp，2 条原始非单调 | 2 条非单调 | 2 条仍非单调 |
| k²，75 条原始递增 | 75 条递增 | 73 条递增，2 条非单调 |
| k²，23 条原始非单调 | 23 条非单调 | 13 条递增，10 条仍非单调 |

fp 的两个原始反例位于面积/FL：3500/0.75、4500/1.0（μm²/μm）。每次反向变化是 −0.2 MHz，处于局部半步长传播包络内；在这两个序列未发现 prominence≥1 dB 的近幅竞争峰，也未发现最近频率峰追踪与主峰选择分离。只能说没有检测到本方法定义的选峰切换，不能证明物理模态身份或排除数值误差。

k² 有 27 次原始下降，其中 **4 次、涉及 3 条序列**仍超出局部半步长扰动范围，且同峰插值后仍下降：

| 面积 μm² | FL μm | EG 变化 μm | 原始 k² 变化（百分点） | 插值后变化 |
|---:|---:|---|---:|---:|
| 900 | 1.0 | 1.5→2.0 | −0.016628 | −0.009849 |
| 900 | 1.5 | 1.0→1.5 | −0.033333 | −0.021074 |
| 900 | 1.5 | 1.5→2.0 | −0.018592 | −0.032097 |
| 4500 | 1.0 | 1.5→2.0 | −0.015535 | −0.009202 |

这些都是固定其余记录条件的仿真对照，不是实测规律。COMSOL 缺少电学损耗；本轮也没有验证网格收敛、材料、厚度或真实工艺可迁移性。因此不能将剩余反例删除后强制全局单调。

## 4. 一个“单调”的序列实际上发生了所选峰切换

面积 4500 μm²、FL=2 μm，EG 从 1.0 到 1.5 μm 时，自动选择的 fp 从 5.5206 跳到 5.5276 GHz。两处竞争峰幅度差仅约 0.046–0.059 dB；最近频率峰延续在低频峰，而最高峰选择转到高频峰。这是可直接复查的**所选谱峰切换**，不等同于已确认的物理本征模态切换。

对应行号 TR-0186、TR-0282、TR-0378、TR-0474，序列 `80958e9d857c267b`。该序列在 P6A 被判为递增，说明单调性本身也不能证明模态连续。

[峰形证据图](/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p6b_20261022/review_figures/peak_evidence.png)中，前两行是 fp 轻微反例，第三行是竞争峰切换；圆点是冻结算法选中的 fp。[小面积 Qp 对照图](/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p6b_20261022/diagnostics_v2/small_area_qp.png)显示同一结构的预测区间与观测中位数。

## 后续算法实验边界（交 Astra）

优先顺序如下，这些是下一轮建议，不是已完成的模型：

1. 保留 P3 Extra Trees 和 P6A GP。围绕 EG 边界的均值收缩，设计“趋势项 + 残差”与各维尺度的最小消融；围绕小面积 PF 单独审查几何尺度及聚合标签不确定性。先在训练内设计组验证中选方法，再统一对已见外层留出作回顾性报告。
2. 聚合标签不确定性与预测区间要分开建模/报告；不直接把位置 MAD 当 GP 噪声方差，也不把 CI 重叠重新计成覆盖成功。PF 的小校准组数必须明确报告。
3. 暂不引入 EG→k² 硬单调约束。fp 软先验也要排除选峰不连续、局部分辨率不够和元数据不全的情形，再核验实测训练折中的条件性规律；真实 AG/各层厚度仍未确证，不以 AG=1 占位对齐。
4. 若要重新 COMSOL 求解，应先验证对应 MPH/损耗/网格与许可证；本轮只读了现有 s1p，没有启动求解。独立加密扫频和网格收敛证据才能进一步区分轻微反向变化与数值因素。
5. 新尺寸最优解仍须独立实测。已有标量/曲线诊断不允许提升 P4/P5 的可信范围，也不允许跨 raw/de 或混合仿真标签训练。

## 失败记录与验证

初版诊断错误使用全频段最大步长 2 MHz，导致量化包络过宽。已保存 `diagnostic_v1_sources/`、`diagnostic_v1_correction.json` 和初版 `diagnostics/`；新增混合步长回归测试先复现失败，再修复为 fs/fp 局部步长。最终结果在 `diagnostics_v2/`，未改旧提参器或 P6A 结果。

验证：P3/P4/P6A/P6B 共 38 项测试通过；新增文件 Ruff、两个 worktree 的 diff 检查通过。独立检查 944 条实测诊断记录、480 条冻结 test 预测对应关系、390 个原始文件与局部频率步长、584 条指标配对。110 个既有 dirty 文件、P3/P4/P5 的 71/92/39 个产物和 P6A 的 74 个产物保持不变。

主要诊断表：`measured_residuals.csv`、`measured_branch_summary.csv`、`small_area_qp.csv`、`small_area_qp_frozen_baselines.csv`、`simulation_peak_probes.csv`、`eg_series_diagnostics.csv`、`eg_pair_diagnostics.csv`。`verification.json` 为独立核验收据；交付清单另见 `delivery_manifest.json`。

完整重跑已保存到 `reproduction/diagnostics_v2/`，上述 7 个 CSV 与首次最终运行字节一致，核对见 `reproduction_check.json`。

在 ML worktree 的 backend 复现，输出目录须不存在：

```bash
uv run --no-sync python scripts/run_optimization_p6b.py --p3-root surrogate_data/optimization_p3_20261015 --p6a-root surrogate_data/optimization_p6a_20261022 --protocol /Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/docs/superpowers/plans/2026-10-22-surrogate-p6b-protocol.md --out surrogate_data/p6b_reproduce_new/diagnostics_v2
uv run --no-sync python scripts/render_optimization_p6b.py --diagnostics surrogate_data/optimization_p6b_20261022/diagnostics_v2 --simulation-table surrogate_data/optimization_p6a_20261022/simulation_audit_v2/simulation_extraction.csv --out surrogate_data/p6b_reproduce_new/review_figures
uv run --no-sync python scripts/verify_optimization_p6b.py --root surrogate_data/optimization_p6b_20261022 --p6a-root surrogate_data/optimization_p6a_20261022
uv run --no-sync pytest -q tests/ml/test_optimization_p3.py tests/ml/test_optimization_p4.py tests/ml/test_optimization_p6a.py tests/ml/test_optimization_p6b.py
```
