# P6D：选择规则与训练内边界压力测试

2026-10-22。已完成。本轮发现：**分支均衡选择没有改变 P6C 的结论；按最差训练内边界单元选模型，反而恶化了三个外层留出场景的误差。小面积 PF 的 Qp 仍 0/8 命中。** 原 P3/P4/P5 继续保留，没有新外层模型、校准或产品发布。

[冻结协议](2026-10-22-surrogate-p6d-protocol.md)规定本次选择规则；[P6C](2026-10-22-surrogate-p6c-results.md)保留四配方及全部外层预测。本轮所有外层数字都来自已查看过的 P6C，仍属于回顾性研究，不能称为新盲测。

工作目录 `/Users/jingbozuo/Projects/aln-data-backend-ml/backend`；产物 `surrogate_data/optimization_p6d_20261022/experiment/`。没有仿真标签/伪标签、raw/de 混合、AG/厚度占位或特殊拓扑猜测。

## 本轮实际完成

1. 复用 P6C 的四配方与训练内四折预测，将评分改为分支/目标单元等权。
2. 在每个外层拟合集内部，对 EG、FL、面积分别留出本分支最低/最高已有水平，共六种伪边界。涉及同一 design_group 的非边界结构也移出训练，记为 embargo；仅评分边界结构。
3. 对各场景/分支/目标的设计组平均绝对 log 误差取最大值，选择最大值最小的配方；同时保存所有单元与跳过原因。
4. 先保存选择，再读取冻结 P6C 外层预测报告结果。未重新拟合外层模型、重新校准区间或修改标签。

每个外层拟合集有 36 个场景/分支组合，其中 22 个有效、14 个因该轴少于三个水平而跳过。部分有效单元仅 1 个评估设计组；这不是足够样本的鲁棒性证明。四个 scope、四配方、四目标共记录 1408 个评分单元及 19712 条内部预测。

## 选择发生了什么

| 外层划分 | P6C 平均分 | 分支均衡四折 | 最差伪边界单元规则 |
|---|---|---|---|
| 普通结构 | trend_gp_log_area | trend_gp_log_area | gp_raw |
| 面积两端 | trend_gp_log_area | trend_gp_log_area | trend_gp_log_area |
| EG 上边界 | trend_gp_log_area | trend_gp_log_area | gp_log_area |
| FL 上边界 | trend_gp_log_area | trend_gp_log_area | gp_raw |

分支均衡后仍四次选中同一配方，所以“大分支数量更多”不是这四配方中选择失败的唯一解释。

最差单元规则容易被特定区域/少量组支配。例如 EG 外层拟合集内部，四配方的最坏单元均为“面积最低端、PF=1、无 FL、Qp”，且只有 1 个设计组；该规则优先压低这个单元，并不保证 EG 外层误差改善。普通/FL 的最坏单元也主要集中在小面积 PF Qp。

## 外层结果：新规则没有带来改进

下表是最差伪边界规则相对 P6C 平均分选择的 MAE，表示为“P6C → 本轮规则”。分支均衡策略与 P6C 完全相同。k² 单位为百分点。

| 留出 | k² | Qs | Qp | Bode Q |
|---|---:|---:|---:|---:|
| 普通结构 | 0.064→0.109 | 14.804→18.920 | 29.217→32.653 | 21.284→21.778 |
| 面积两端 | 0.154→0.154 | 20.479→20.479 | 41.786→41.786 | 27.252→27.252 |
| EG 上边界 | 0.193→0.734 | 19.411→40.326 | 85.721→159.584 | 83.323→129.674 |
| FL 上边界 | 0.104→0.205 | 16.978→27.755 | 34.142→49.875 | 38.253→49.162 |

归一化联合区间的四目标设计组共同覆盖：

| 留出 | P6C/分支均衡 | 最差伪边界规则 |
|---|---:|---:|
| 普通结构 | 32/34，94.1% | 28/34，82.4% |
| 面积两端 | 28/48，58.3% | 28/48，58.3% |
| EG 上边界 | 48/48，100% | 47/48，97.9% |
| FL 上边界 | 34/36，94.4% | 36/36，100% |

FL 覆盖增加伴随点误差恶化；其区间直接复用 P6C 对应配方，不是新增校准带来的改善。完整全局/归一化区间、宽度和漏覆盖点均保留，未仅展示这张覆盖表。

面积外层三种选择完全相同，因此小面积 PF Qp 仍为 0/8 命中，MAE=118.761。训练内面积最低端是 900 μm²，所选配方在 7 个 PF 结构上的 Qp MAE=43.761；外层是 700 μm² 的 8 个结构。这不是完全配对的同一结构集合，不能解释成面积的因果效应，但明确显示训练内伪边界表现不能替代真实留出端点验收。

## 判断与下一步

本次失败只否定了**这套冻结的最差单元评分在当前四配方上的改进效果**，不证明所有边界验证方法无效，也不证明数据无法建模。

当前不再继续堆叠这四配方的选择规则或按外层 test 改权重。P6A–P6D 已分别检查固定 GP、趋势/面积表示、均衡评分和伪边界评分；小面积 PF 和 EG 边界仍是明确薄弱点。更值得优先核查的是：

- **实测标签层面**：回到 P3 已清理记录，审查小面积 PF 各坐标/重复观测及去嵌、提参质量；不重做整批整理、不覆盖原聚合标签。P6B 发现的宽观测 CI 需要与模型误差分开处理。
- **验证可辨识性**：明确哪些分支只有 1–数个独立设计组，是否足够支撑新的层级误差模型或选择规则。位置 MAD 不可直接替代均值/中位数估计方差。
- **外推与新实测**：未来候选及预测须事先冻结，且需要独立实测。已有 COMSOL 无电学损耗，尤其不能用其 Q 补实测。真实 AG/层厚、制造范围和工程容差未确认前，不生成可下单新尺寸。

这些是下一阶段任务，不在本轮声称已完成。现有四目标寻优页仍依据冻结 P3/P4；本轮全部记录 `trusted_for_unmeasured_optimization=false`，没有扩大可信支持范围。

## 文件与复现

每 scope 保存 `*_stress_manifest.csv`、`*_capacity.csv`、`*_stress_predictions.csv`、`*_stress_cells.csv`、`*_balanced_cells.csv`。`selection.json` 保存两个新评分规则及 P6C 对照。外层文件为 `outer_predictions.csv`、`outer_metrics.csv`、`joint_coverage.csv`、`interval_failures.csv`、`small_area_pf_qp.csv`。

独立核验：19712 条内部预测、1408 个评分单元、7968 条外层记录逐条对应冻结 P6C；训练/embargo/评估分组、跳过条件和选择分数均复核。44 项回归测试通过，Ruff 和两个 worktree 的 diff 检查通过；123 个既有 dirty 文件和 P3/P4/P5/P6A/P6B/P6C 的 71/92/39/74/56/58 个产物保持不变。

在 ML worktree 的 backend 执行，输出使用未存在的新目录：

```bash
uv run --no-sync python scripts/run_optimization_p6d.py --p3-root surrogate_data/optimization_p3_20261015 --p6c-root surrogate_data/optimization_p6c_20261022 --protocol /Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/docs/superpowers/plans/2026-10-22-surrogate-p6d-protocol.md --out surrogate_data/p6d_reproduce_new/experiment
uv run --no-sync python scripts/verify_optimization_p6d.py --root surrogate_data/optimization_p6d_20261022
uv run --no-sync pytest -q tests/ml/test_optimization_p3.py tests/ml/test_optimization_p4.py tests/ml/test_optimization_p6a.py tests/ml/test_optimization_p6b.py tests/ml/test_optimization_p6c.py tests/ml/test_optimization_p6d.py
```

本轮没有 Git 提交、模型部署或 COMSOL 求解。完整交付和验证日志见产物根目录的 `delivery_manifest.json` 与 `validation_commands.json`。

完整重跑保存在 `reproduction/experiment/`，26 个 CSV/选择文件字节一致，核对收据为 `reproduction_check.json`。
