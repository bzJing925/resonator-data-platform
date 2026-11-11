# P11：Qp/k² 联合外推候选推荐结果

2026-10-26。用户授权本轮给出外推结构参数建议。结论：**在协议冻结的搜索空间内，没有任何候选的点预测或区间下界超过实测基线或支配实测前沿——冻结模型不支持"预测更优"的表述**；推荐以"待实测验证假设"形式给出：A 组（EG 外推）4 个 + B 组（前沿内插）6 个候选，附双模型对照、风险标注与最小证伪方案。C 组排除方向保留记录。

依据：[P11 冻结协议](2026-10-26-surrogate-p11-protocol.md)（运行前保存；含一处修订记录：原 C 组演示点命中不存在分支被模型正确拒绝，改为 (EG=0, FL=2.5, 700, PF=1)）。工作目录 `/Users/jingbozuo/Projects/aln-data-backend-ml/backend`；产物 `surrogate_data/optimization_p11_20261026/`。

## 目标、基线与前沿

联合目标 Qp、k² 均越大越好。基线（从 P4 实测目录重算并断言一致）：Qp 冠军 C6-1（EG1/FL1.5/5500/PF0，Qp=1395.96，k²=14.014%）；k² 冠军 E1-1（EG2/FL0/5500/PF0，k²=14.674%，Qp=1179.63）。实测 (Qp,k²) Pareto 前沿 6 点（C6-1、E6-1、E5-1、B2-1、E2-1、E1-1），全部在 area=5500、PF=0。

## 候选与逐目标预测

主预测为冻结 P3 ExtraTrees 包（自带区间与支持标签）；敏感性对照为 P6C trend_gp_log_area 保存模型点预测。全部 11 个候选（10 个 A/B + 1 个 C 演示）：

| 候选 | EG | FL | Qp 点预测 [P3 区间] | k² 点预测 [P3 区间] | 支持 | 推荐级别 |
|---|---:|---:|---|---|---|---|
| A00 | 2.5 | 0 | 1135.5 [1007.6, 1279.6] | 14.658 [14.169, 15.164] | outside_training_bounds | 待实测验证 |
| A01 | 2.5 | 0.5 | 1205.0 [1069.3, 1358.0] | 14.454 [13.971, 14.953] | outside_training_bounds | 待实测验证 |
| A02 | 3.0 | 0 | 1135.5 [1007.6, 1279.6] | 14.658 [14.169, 15.164] | outside_training_bounds | 待实测验证 |
| A03 | 3.0 | 0.5 | 1205.0 [1069.3, 1358.0] | 14.454 [13.971, 14.953] | outside_training_bounds | 待实测验证 |
| B04 | 1.25 | 1.25 | 1208.9 [1072.8, 1362.3] | 14.131 [13.612, 14.569] | within_hull_new_level | 插值候选 |
| B05 | 1.25 | 1.5 | 1345.1 [1193.6, 1515.8] | 14.017 [13.493, 14.441] | within_hull_new_level | 插值候选 |
| B06 | 1.25 | 1.75 | 1243.4 [1103.4, 1401.2] | 13.882 [13.493, 14.441] | within_hull_new_level | 插值候选 |
| B07 | 1.0 | 1.375 | 1282.6 [1138.2, 1445.4] | 14.082 [13.612, 14.569] | within_hull_new_level | 插值候选 |
| B08 | 1.0 | 1.625 | 1318.2 [1169.8, 1485.5] | 13.959 [13.493, 14.441] | within_hull_new_level | 插值候选 |
| B09 | 2.0 | 0.25 | 1205.0 [1069.3, 1358.0] | 14.454 [13.971, 14.953] | outside_training_bounds | 插值候选（FL 低于分支内层最小水平） |
| C10 | 0 | 2.5 | 527.4 [468.0, 594.3] | 4.800 [4.639, 4.965] | outside_training_bounds | **不推荐（演示）** |

（C10 为 700-PF 分支 FL>2 方向的风险标注演示，其点预测也远低于基线。）

敏感性对照（P6C trend_gp 点预测）：A 组 EG=2.5/3.0 的 Qp 为 1142.7/1082.0、k² 为 14.691/14.607——与 P3 同为平坦或回落，**两个冻结模型都不预测 EG 外推带来增长**。B05 的 trend_gp Qp=1323.4，与 P3 的 1345.1 同向低于基线。

与基线对比（冻结双口径）：全部候选 Qp 点预测相对 C6-1 为 −50.8…−868.6，k² 相对 E1-1 为 −0.02…−9.87 个百分点；区间下界相对基线全部为负；没有任何候选在点预测或下界口径上支配前沿任何一点。上行可能仅以区间上界表达：A00/A02 的 k² 上界 15.164 高于 E1-1 的 14.674；B05 的 Qp 上界 1515.8 高于 C6-1 的 1395.96。

分层排名（点预测口径）：Qp 优先前两名 B05（1345.1）、B08（1318.2）；k² 优先前两名 A00/A02（14.658，并列内容因树模型平坦外推）；均衡 Pareto（候选内）：B04、B05、B07、A01 等。保守口径（区间下界）排名见 `rankings.csv`。

## 外推风险与适用边界

- A 组依据：仿真 EG↑→k²↑（P6A 75/98、CONSISTENT 14/14；P6B 保留 3 序列 4 次反例）+ 实测 5500/FL=0 的 Qp 在 EG=2 仍上升；**但两个冻结模型均不预测增长**，推荐强度为"有待证伪的上行假设"。
- B 组为凸包内新水平插值，风险低于 A 组；B09 的 FL=0.25 低于 fl1 分支内层最小 FL（0.5），支持标签如实降级为 outside_training_bounds。
- 已知失效边界全部生效：700-PF Qp（0/8 失败史）、area>5500（面积边缘 k² 覆盖 64.6%）、FL>2（55.6%）、EG>2 且 FL≥1（实测回落）；这些方向列入 `excluded.csv`，不推荐。
- COMSOL 无电学损耗：Qp 无仿真先验；仿真仅提供 k² 方向性证据。P6H 的组间解耦结论（Qp 与 k²/fp 不联动）意味着联合改善不能由单一机理预期，须逐目标验证。

## 最小实测验证方案要点

详见 `falsification_plan.md`（测量前冻结）。要点：版图/工艺确认后同工艺流片；每结构 ≥12 个坐标、同批次、original/vz 去嵌（P6G 已验证可复现）；同批含 C6-1/E1-1 复测对照；通过 = 典型值落入 P3 区间且 Qp 或 k² 至少一项超过对应基线；失败方向降级为开发数据。建议最小集合：A 组 4 个 + B 组下界最接近前沿的 2 个（B05、B08）；C 组不进入制造。

## 判断

1. 冻结模型在本搜索空间内不支持"预测更优"——推荐的全部是**待实测验证的假设**，上行证据来自趋势与区间上界，不是点预测。
2. 若用户只接受"模型点预测即超基线"的候选，则本轮答案是**没有**，这比强行给候选更符合证据。
3. P5 离线页未扩展：外推候选不是已验证目录，混入会污染其"冻结目录浏览"契约；候选以本文档 + CSV + 验证方案形式交付评审。

## 复现与验证

新增源码：`app/ml/optimization_p11.py`、`scripts/run_optimization_p11.py`、`scripts/verify_optimization_p11.py`、`tests/ml/test_optimization_p11.py`。产物：candidates.csv、rankings.csv、measured_frontier.csv、excluded.csv、falsification_plan.md、frozen_inputs.json、summary.json、outputs_manifest.json、verification.json、reproduction_check.json。

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-ml/backend
uv run --no-sync python scripts/run_optimization_p11.py \
  --p3-root surrogate_data/optimization_p3_20261015 \
  --p4-root surrogate_data/optimization_p4_20261016 \
  --p6a-root surrogate_data/optimization_p6a_20261022 \
  --p6c-root surrogate_data/optimization_p6c_20261022 \
  --protocol /Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/docs/superpowers/plans/2026-10-26-surrogate-p11-protocol.md \
  --out surrogate_data/p11_reproduce_new
uv run --no-sync python scripts/verify_optimization_p11.py \
  --root surrogate_data/p11_reproduce_new \
  --p4-root surrogate_data/optimization_p4_20261016 \
  --out surrogate_data/p11_reproduce_new/verification.json
uv run --no-sync ruff check app/ml/optimization_p11.py scripts/run_optimization_p11.py scripts/verify_optimization_p11.py tests/ml/test_optimization_p11.py
```

验证：Ruff 通过；tests/ml 全套 **110 项通过**（含 P11 新增 3 项）；独立核验 **10/10**（源码不变、冻结网格一致、前沿重算 6 点一致、基线与冻结冠军一致、无候选点预测超基线、C 组标记不推荐且带失败史标注、排名与候选一致、证伪方案覆盖全部 A/B 候选与基线、排除方向保留、产物清单完整）。完整重跑：文本与 JSON 字节一致；candidates/rankings 数值列按 P4 惯例以 1e-9 绝对容差一致（ExtraTrees 跨进程浮点累加抖动 ~1e-12）。未改产品代码/标签/划分/冻结制品；既有 dirty 文件未触碰；无 Git 提交、无部署。
