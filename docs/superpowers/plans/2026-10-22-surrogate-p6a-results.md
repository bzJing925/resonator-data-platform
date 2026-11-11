# P6A 回顾性算法对照与独立 COMSOL 规律审计

日期：2026-10-22。结论：完成 P6A，保留 P3 模型与 P4/P5 产品；没有得到可交付的新尺寸最优解，也没有完成独立实测外推验收。

## 证据与边界

[冻结协议](2026-10-22-surrogate-p6a-protocol.md)在新增模型运行前保存。原 P3 的 576 个去嵌结构、设计组及四个划分原样复用，calibration/test/embargo 不参与拟合或缩放。全部留出以前已查看，本次只能称回顾性比较。没有读取 raw 标签参与拟合，没有加入 COMSOL 标签或伪标签，没有调整模型追逐本轮测试结果。

ML 工作目录：`/Users/jingbozuo/Projects/aln-data-backend-ml/backend`。结果根目录：`surrogate_data/optimization_p6a_20261022/`。没有提交或发布本轮代码。

## 模型对照结果

固定 Matérn GP 的点预测 MAE 如下，括号为相对 P3 Extra Trees 的比例，小于 1 表示误差减小。k² 单位为百分点，Q 无量纲。GP 两种校准方式共用相同点预测。

| 回顾性留出 | k² | Qs | Qp | Bode Q |
|---|---:|---:|---:|---:|
| 普通结构 | 0.109（0.824） | 18.920（1.002） | 32.653（0.841） | 21.778（0.673） |
| 面积两端 | 0.326（0.804） | 31.261（0.813） | 48.595（0.860） | 27.106（0.926） |
| FL 上边界 | 0.205（0.745） | 27.755（1.111） | 49.875（0.780） | 49.162（0.684） |
| EG 上边界 | 0.734（8.080） | 40.135（2.341） | 161.188（3.898） | 130.734（3.799） |

Ridge 在四个 scope、四个目标的 MAE 均劣于 P3 Extra Trees（约 1.37–5.14 倍），保留作失败对照。GP 仅部分区域有改善，尤其 EG 边界显著恶化；不能整体替换 P3，也不能根据已见 test 把不同模型的赢家拼成新的已验证系统。

四目标共同区间按“同一设计组中本次评估的所有结构、四个指标全部命中”计数：

| 留出 | P3 逐目标校准 | GP 逐目标校准 | GP 四目标联合校准 | 联合区间宽度/P3（四目标范围） |
|---|---:|---:|---:|---:|
| 普通结构，34 组 | 23/34，67.6% | 24/34，70.6% | 28/34，82.4% | 1.04–1.99 |
| 面积两端，48 组 | 8/48，16.7% | 6/48，12.5% | 23/48，47.9% | 1.03–1.58 |
| FL 上边界，36 组 | 7/36，19.4% | 26/36，72.2% | 36/36，100% | 2.24–4.21 |
| EG 上边界，48 组 | 41/48，85.4% | 41/48，85.4% | 48/48，100% | 4.27–7.21 |

宽度比例是各目标的中位绝对区间宽度比。EG 的高覆盖伴随更大误差和显著更宽区间，不是外推成功；面积共同覆盖仍远低于名义 90%。面积 700 μm² 的 GP 联合校准区间，Qp 结构覆盖仅 62.5%、k² 79.2%；5500 μm² 四目标的各自结构覆盖均为 100%。不能把两端合并统计当成小面积可靠。

3984 条新增预测均有限，未发现非正预测或 k²≥100% 的点预测；这仅是数值检查，不代表物理验证。输出包括分支级指标、偏差、最差误差、Top-5、单目标与联合覆盖、全部漏覆盖记录。

主要文件：

- `pilot/new_predictions.csv`、`metrics.csv`、`comparison_to_p3.csv`、`joint_coverage.csv`、`interval_failures.csv`。
- `pilot/calibration.json`、`fit_receipts.json`、`frozen_recipe.json`、`retrospective_models.joblib`、`outputs_manifest.json`。
- `reproduction/pilot/` 和 `reproduction_check.json`：完整重跑的 7 个结果表/JSON 字节一致；32 个保存模型在全部 576 个结构的 log 均值和标准差完全一致。joblib 二进制哈希不同，不声称模型文件字节一致。

## 独立仿真审计

用户明确：COMSOL 没有加入电学损耗，串联峰虚高，尤其 Q 与实测不对齐。仿真只能独立研究规律，不能和实测一起拟合；本轮严格执行。

原始文件实际位置：`/Users/jingbozuo/Downloads/L_air = L_bot-6[um]-2`。对照表：`/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/D1_D2_DOE_SNP_training_map_Lair_Lbot_minus6um.xlsx`。旧项目内同名目录没有原始 s1p。

- 对照表 1435 行：1339 条 MATCHED 全部按完整相对路径找到，另 96 行不加入。原目录共 1339 个 s1p，无孤立文件。文件名存在重复，未按 basename 合并。
- 原生扫频有 4.20–5.90 GHz、3.69–5.39 GHz 两类；1339 个均通过单端口、有限值、频率递增、自动提参和谐振顺序检查。使用 ML worktree 当时的提参器和显式 AlgorithmConfig，完整源码哈希与配置已记录；不声称与主仓库后续提参版本或历史 CSV 的 Q 完全等价。
- 12 条 has_PF=1 但 PF 厚度缺失，隔离并列出行号，未补猜；1327 条允许进入受控仿真趋势搜索。
- 628 条表内标记 CONSISTENT，603 条 KNOWN_VERSION_OVERRIDE，108 条 USER_DECISION_APPLIED。保留全部原始审计标记，同时单独报告 CONSISTENT 子集；本轮未独立验证历史人工决策或 MPH 设置。
- 配对固定 Stage、Template、Variant、EG 状态、拓扑标记、其他物理尺寸和扫频范围；每序列至少 3 个尺寸水平。同一尺寸存在重复时整条序列拒绝，不平均。面积变化伴随 top/bottom/air 轮廓联动，面积趋势应解释为这组几何共同变化，不能声称纯面积因果效应。
- 全部可审计数据得 393 条几何序列；CONSISTENT 子集 69 条，是前者的子集而非独立重复。六指标展开共 2772 条序列指标、14598 条相邻配对记录。一个面积重复序列在两种 cohort 中均被拒绝，故拒绝表 2 行。

可复查的初步规律：

| 维度与指标 | 全部可审计序列 | CONSISTENT 子集 | 判断 |
|---|---|---|---|
| EG 宽度 → fp | 98 条中 96 条递增，2 条非单调 | 14/14 递增 | 可作为待核实趋势候选 |
| EG 宽度 → k² | 98 条中 75 条递增，23 条非单调 | 14/14 递增 | 依赖条件，不能全局强制 |
| FL 宽度 → Bode Q | 126 条中 122 条非单调 | 28/28 非单调 | 不支持简单单调先验 |
| 面积/轮廓 → Qs | 125 条中 124 条非单调 | 25/25 非单调 | 不支持简单单调先验 |

还没有逐条做模态连续追踪、网格收敛或损耗敏感性核查；频率平坦也可能受扫频离散分辨率限制。本轮没有把任何趋势写入实测模型，没有完成仿真到实测的可迁移性验证。EG/AG 高度在保守控制条件下没有形成至少三个水平的有效序列，不能据此提出高度规律。

仿真文件在 `simulation_audit_v2/`：`simulation_extraction.csv` 保留完整映射与来源哈希；`quarantine.csv`、`unmatched_map_rows.csv`、`rejected_series.csv` 保留排除证据；`trend_series.csv`、`trend_pairs.csv`、`trend_directions.csv` 可定位具体结构对。

首次 `simulation_audit/` 失败也保留：错误要求完整覆盖旧脚本的 4.25–7 GHz，1339 条全部拒绝，随后空趋势表触发 KeyError。`failure.json`、`failed_runner.py` 保存原因和当时源码；修复仅改变独立仿真审计的扫频口径和空结果处理，没有改实测实验协议或模型。

## 支持规则与下一步决定

1. 继续使用冻结 P4 支持规则：明确实测点或同分支完整局部网格角点；凸包/距离仅作诊断，不足以宣称可信预测。所有新增预测均 `trusted_for_unmeasured_optimization=false`，不进入新结构可信排名。
2. 本轮不升级到更复杂算法。先核查面积 700 μm² 的 Qp 失败、EG 上界 GP 误差，以及仿真 EG 趋势中的反例和峰的连续性。以失败记录定位，不删反例重报结果。
3. 若要引入规律，只能先定义条件性的软约束或物理特征，再用实测训练折独立拟合、做消融；COMSOL 数值不得成为训练标签或混合样本。真实 AG、各层厚度与拓扑需要确证，不能沿用旧模型 AG=1 占位。
4. 后续算法设计可由 Astra 接手。沿用现有结果作回顾性对照；任何参数搜索结果都不能重新命名为盲测。不要凭本轮已见 test 选择子区域赢家再宣称独立验证。
5. 真正新尺寸外推仍需要独立实测：先确认工艺/厚度/AG、制造步长和实际可做范围，再冻结候选、预测区间、四目标评价容差、去嵌/提参版本及重复/对照方案，之后采样。当前没有用户提供的新尺寸实测数据，不生成未经确认的可下单尺寸。

## 复现与验证

在 ML worktree 的 backend 执行；输出目录必须是尚不存在的新目录：

```bash
uv run --no-sync python scripts/run_optimization_p6a.py --p3-root surrogate_data/optimization_p3_20261015 --protocol /Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/docs/superpowers/plans/2026-10-22-surrogate-p6a-protocol.md --out surrogate_data/p6a_reproduce_new/pilot
uv run --no-sync python scripts/audit_optimization_sim_p6a.py --snp-root '/Users/jingbozuo/Downloads/L_air = L_bot-6[um]-2' --map /Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/D1_D2_DOE_SNP_training_map_Lair_Lbot_minus6um.xlsx --out surrogate_data/p6a_reproduce_new/simulation_audit_v2
uv run --no-sync python scripts/verify_optimization_p6a.py --root surrogate_data/optimization_p6a_20261022
uv run --no-sync pytest -q tests/ml/test_optimization_p3.py tests/ml/test_optimization_p4.py tests/ml/test_optimization_p6a.py
```

已验证：34 项测试通过；新增文件 Ruff 通过；两个 worktree 的 `git diff --check` 通过。独立核验 48 个校准分位数、3984 条保存模型预测、1339 个原始文件哈希和 14598 个配对的控制变量/比值。P3/P4/P5 的 71/92/39 个结果文件，以及本轮开始登记的 103 个既有 dirty 文件保持不变。

明确例外：P3 收据引用的旧临时 HANDOFF.md 已不在系统临时目录，无法复核其内容；没有将此项当成通过。项目内 P3 协议/结果与冻结产物仍通过哈希检查。新交接以本结果文档及可复现产物为持久依据。
