# ALN 结构寻优：P4 结果与接手说明

日期：2026-10-16。**受限 P4 核心已实现并运行；未开放新尺寸可信寻优，未完成 P5 页面或 P6 独立实测。**

本轮直接使用 P3 冻结结构表、划分、模型和预测结果，没有重新整理工作簿、重训或替换旧模型。所有新增 ML 源码位于 `/Users/jingbozuo/Projects/aln-data-backend-ml`。原有 dirty 文件和 P3 结果保留；无提交、部署、数据库或旧 HTML 修改。

## 1. 入口

- [本轮冻结协议](2026-10-16-surrogate-p4-protocol.md)
- [P3 结果与原始复现说明](2026-10-15-surrogate-p3-results.md)
- 程序：`/Users/jingbozuo/Projects/aln-data-backend-ml/backend/scripts/run_optimization_p4.py`
- 核心：`backend/app/ml/optimization_search.py`
- 独立核验：`backend/scripts/verify_optimization_p4.py`
- 测试：`backend/tests/ml/test_optimization_p4.py`
- P3 输入根目录：`/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p3_20261015/`
- P4 输出根目录：`/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p4_20261016/`

以下相对输出路径均相对于 P4 输出根目录。`measured/results.json` 汇总四项 Top-10、完整性能向量和 Pareto 集；CSV 保留全部原始精度、来源和区间字段。

## 2. 边界与不确定性审查

P3 标称 90% 区间校准的对象是一个设计组内的结构典型值，单目标分别校准。它不保证四目标联合覆盖，也不保证优化后选中的冠军、单器件表现、未来批次或外推区域。当前测试和边界结果已被查看，后续分析必须标为回顾性。

| 已见评估集合 | 设计组数 | 四目标均覆盖的设计组 | 比例 |
| --- | ---: | ---: | ---: |
| 普通结构留出 | 34 | 23 | 67.6% |
| 面积两端留出 | 48 | 8 | 16.7% |
| FL 上边界留出 | 36 | 7 | 19.4% |
| EG 上边界留出 | 48 | 41 | 85.4% |

“设计组覆盖”只检查该次评估中实际留出的成员；不是被 embargo 成员的推断。上述为有限样本描述性比例，不是总体保证。四项目标并未在 P3 声称联合 90%，因此这张表说明联合使用的局限，不能把它当作违背原单目标校准契约。

单项目标的明显失效仍然存在：面积边界 k² 为 62/96 = 64.6%；FL 上边界 BodeQ_fitted 为 40/72 = 55.6%。普通留出中，k² 在凸包内新水平点仅覆盖 1/4，在凸包外点覆盖 5/8。样本很小，但已足以否定“凸包内即可信”的用法。

例：结构 `s_c337eeb03f645a0d` 被 P3 标为 `within_hull_new_level`，k² 实际 7.6030%，预测 8.6288%，区间 [8.3407%, 8.9268%]。面积边界结构 `s_0182a3be6734f9b2` 的 k² 实际 6.2713%，预测 8.1035%，区间 [7.6925%, 8.5365%]。

六个分支的校准设计组数为 1、6、3、25、1、6。按既有 split-conformal 有限样本秩规则，五个分支不足以独立给出有限的 90% 分支区间。不能通过直接分支校准就宣称修复此问题。

审计文件：

- `audit/coverage_by_scope_branch_support.csv`：按目标、分支、支持等级统计。
- `audit/joint_four_target_coverage.csv`：上表可复算证据。
- `audit/interval_failures.csv`：144 条漏覆盖记录，跨不同留出实验，不代表 144 个互不重复结构。
- `audit/branch_calibration_capacity.csv`：分支样本量与有限样本秩检查。

方法依据：[Conformal 教程](https://arxiv.org/abs/2107.07511)；[ExtraTrees 官方文档](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.ExtraTreesRegressor.html)。本轮没有为已见测试扩大区间、重新调参或改变训练集。

## 3. 算法选择与支持规则

保留冻结 ExtraTrees。有限网格直接枚举，暂不增加 GP、贝叶斯优化或 NSGA-II。当前主要缺口是边界证据、校准样本和物理信息；换算法本身不能补齐这些缺口。

新程序区分两种模式：

1. **measured**：在 P3 的 576 个可靠去嵌结构上排名。默认要求四目标各至少 10 个有效坐标。保留每结构全部四项目标、bootstrap 区间和坐标统计，不拼接不同结构的峰值。
2. **exploratory**：按用户显式网格批量预测，每批最多 512 点，总枚举预算 10,000 点。候选始终标记 `trusted_for_unmeasured_optimization=false`。

探索模式逐目标检查同一拟合分支的局部相邻水平。每个维度若恰好位于训练水平，则固定该水平；否则取相邻上下训练水平。所有笛卡尔积角点必须存在于该目标拟合集，才能标记 `complete_local_cell`；精确训练点单列。缺角、越界、未知分支不进入探索排名。角点 ID、原 P3 支持标签、最近训练结构与距离均可追溯。

该规则故意保守，可能拒绝本可插值的点；它没有经过独立准确性验证，也不是统计覆盖保证。`eligible` 只表示满足该模式的计算规则，不表示可信新结构。已测目录之外的点即使通过局部规则也需要独立实测。

探索网格命中既有实测结构时，仍只使用完整代理预测向量；不混用实测和预测。单目标均最大化；Pareto 以四目标完整向量计算，同值向量全部保留。同分 Top-N 按结构 ID 排序，不主张统计唯一冠军。

## 4. 实际结果

默认实测目录：576 个候选，576 个可行，125 个 Pareto 候选。四项第一名与 P3 完全一致。

| 领先目标 / mark | EG μm | FL μm | 面积 μm² | PF | k² % | Qs | Qp | BodeQ_fitted |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| k² / E1-1 | 2 | 0 | 5500 | 0 | 14.6745 | 347.95 | 1179.63 | 1144.86 |
| Qs / G8-1 | 0 | 2 | 700 | 1 | 4.5284 | 1032.67 | 600.06 | 1018.04 |
| Qp、Bode Q / C6-1 | 1 | 1.5 | 5500 | 0 | 14.0142 | 299.88 | 1395.96 | 1409.22 |

上表每行来自同一结构的典型性能向量。分别有 50、19、48 个实测坐标；不是单器件四指标的共同最大值。P3 的重复与位置敏感性证据仍适用，不能把排名第一称为唯一显著冠军。

| 运行 | 候选数 | 通过约束/局部检查 | Pareto 数 | 含义 |
| --- | ---: | ---: | ---: | --- |
| measured | 576 | 576 | 125 | 默认实测目录 |
| constrained_feasible_example | 576 | 3 | 3 | 仅用于演示配置 |
| constrained_example | 576 | 0 | 0 | 原示例约束无解，保留证据 |
| infeasible_example | 576 | 0 | 0 | k² ≥ 99% 的无解测试 |
| exploratory | 2934 | 1436 | 287 | 仅探索，不是可信推荐 |

可行约束示例：fs 典型值在 [5.0, 5.2] GHz，k²≥12%、Qs≥400、Qp≥1000、BodeQ_fitted≥1000。得到 B6-5、C5-6、A6-6 三个结构。这是程序示例，不是用户工程要求，不能替代实际频带与容差确认。

探索示例采用既有分支尺寸水平及相邻中点，另含越界/未知分支拒绝探针；这些是计算网格，不是已确认制造步长。1,436 个通过者中：424 个精确拟合结构、6 个不在拟合集但已在实测目录中的结构、1,006 个实测目录以外的中间网格点。其余 1,498 个被拒绝，拒绝原因逐行导出。所有代理区间均沿用 P3，均无新尺寸或优化后保证。

实测默认搜索内部计时约 0.18 秒；探索内部计时约 2.31 秒。独立重跑的整条 uv 命令约 2.96 秒，`/usr/bin/time -l` 报告最大 RSS 234,848,256 字节（约 224 MiB），见 `receipts/exploratory_resource_usage.txt`。这是当前机器的观测值，不是性能上限。

## 5. 配置和复现

所有命令在指定 ML worktree 的 backend 下使用本地 uv。输出路径必须不存在，程序拒绝覆盖。下面使用新目录，原始结果可继续保留。

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-ml/backend

uv run --no-sync python scripts/run_optimization_p4.py audit \
  --p3-root surrogate_data/optimization_p3_20261015 \
  --out surrogate_data/optimization_p4_audit_reproduced

uv run --no-sync python scripts/run_optimization_p4.py search \
  --p3-root surrogate_data/optimization_p3_20261015 \
  --config surrogate_data/optimization_p4_20261016/configs/measured.json \
  --out surrogate_data/optimization_p4_measured_reproduced

uv run --no-sync python scripts/run_optimization_p4.py search \
  --p3-root surrogate_data/optimization_p3_20261015 \
  --config surrogate_data/optimization_p4_20261016/configs/exploratory_midpoints_and_rejection_probes.json \
  --out surrogate_data/optimization_p4_exploratory_reproduced

uv run --no-sync python scripts/verify_optimization_p4.py \
  --p3-root surrogate_data/optimization_p3_20261015 \
  --p4-root surrogate_data/optimization_p4_20261016 \
  --out surrogate_data/optimization_p4_verification_reproduced.json
```

核验脚本最后一条检查本次完整交付目录，包括固定示例和两次独立探索运行；它不是任意模型评估器。已存在的核验输出也不能覆盖。

配置允许 `mode`、`bounds`、`minimums`、`top_n`、`min_coordinates`，以及仅探索模式的 `grid`。未知字段会拒绝。`bounds` 为闭区间，可使用 EG、FL、面积、PF，以及仅实测模式的 fs/fp（GHz）；`minimums` 仅支持四个冻结目标。PF 是几何标志，不是 Fail。AG 与特殊拓扑不接受。

```json
{
  "mode": "measured",
  "top_n": 10,
  "min_coordinates": 10,
  "bounds": {"fs_ghz": [5.0, 5.2]},
  "minimums": {"k2eff_pct": 12, "qs": 400, "qp": 1000, "bodeq_fitted": 1000}
}
```

频率约束在实测模式中约束结构典型频率，不代表所有器件都在窗口内。探索模式没有频率代理，若设置频率约束会直接拒绝。目标下限也约束典型点值，不代表器件良率或鲁棒保证。无解时输出空排名、`no_feasible_candidates` 及重叠计数的逐条拒绝原因，不自动放宽条件。

每个运行目录含 `summary.json`、`provenance.json`、`outputs_manifest.json`；搜索另含 `config.json`、`results.json`、候选/可行/Pareto CSV、四个 Top CSV。探索另含 `prediction_details.csv`。输入 P3 清单在运行前后均校验。

## 6. 验收证据与保全

- 94 项相关测试通过：原解析、提参、Touchstone、P3 和新增 P4 测试。详见 `receipts/checks.json`。
- 四个新源码文件 Ruff 检查通过，两个工作区 `git diff --check` 通过。
- 独立核验通过 11 组检查，见 `receipts/verification.json`：约束集合、完整向量、单目标排序、Pareto、P3 哈希、拟合角点来源、覆盖保护、无频率代理的拒绝路径等。
- 两次独立进程的探索排名及 Pareto ID 一致。多线程浮点累加的最大差异约 3.8×10⁻¹²，按绝对容差 10⁻⁹ 核验；不声称预测 CSV 字节完全一致。
- P4 对冻结测试中 68×4 个预测和区间与原 P3 文件按绝对容差 10⁻⁹ 一致。此项是接口一致性检查，不是新盲测。
- P3 清单覆盖的全部文件与本轮记录的原 dirty 源码/既有文档哈希保持一致。没有清理旧训练产物；本轮未写入原始数据、个人数据库或发布模型。
- `receipts/task_source_snapshot.tar.gz` 保存本轮新增源码、测试、两份文档和配置，是源码覆盖包，不是独立安装器；仍依赖既有 backend 本地 uv 环境与 P3 交付。
- `receipts/delivery_manifest.json` 记录本轮产物、源码和文档哈希；不包含自身哈希。

未运行平台全量测试、服务器部署或页面交互验收；这些不在本次独立 CLI 改动范围内。

## 7. 下一阶段与接手提示词

P4 的实测目录寻优可用于筛选与比较；P5 可直接消费统一 JSON/CSV。进一步开放新尺寸推荐之前，仍需确认实际频带、制造约束、目标口径和工程容差，恢复一致的去嵌流程，并安排独立实测。现有单批次、缺失 AG、隔离特殊拓扑和面积冲突的问题没有被算法消除。

若开展复杂模型：先定义它要解决的具体失效区域，保留 P3/P4 作为对照。可在训练/验证部分进行有约束实验；已经查看的 test 和边界集只能报告后续回顾性对照。分支样本不足和分布变化需要补点或物理信息，不能把更窄的后验区间直接视为准确。新尺寸测试必须在测量前冻结模型、候选、提参/去嵌协议和成功标准。

可直接交给下一代理：

> 接手 ALN 结构寻优。先读 `2026-10-16-surrogate-p4-protocol.md`、本结果文档和引用的 P3 冻结协议。P0—P3 和受限 P4 已实际完成，不重做数据整理、不覆盖旧产物。P4 默认实测目录 576 点、Pareto 125 点；探索 2934 点中 1436 点通过局部几何规则，但全部保持不可信新结构标志。四目标联合设计组覆盖在普通留出/面积边界/FL 上边界分别只有 67.6%/16.7%/19.4%；保留失败明细。所有 ML 代码在指定 ML worktree，Python 用本地 uv，保留 dirty 文件。不要猜 AG、特殊拓扑或面积冲突，不混 raw/de，不拼四项目标峰值，不将已见测试称为新盲测。若继续 P5，消费现有结果契约；若研究复杂算法，先冻结具体问题与验证协议，保留 P3/P4 对照。新尺寸外推最终必须有独立实测。
