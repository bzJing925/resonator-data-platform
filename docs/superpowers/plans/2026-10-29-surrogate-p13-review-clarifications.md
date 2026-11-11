# P13 审查后追加澄清与限定范围恢复说明

日期：2026-10-29。
性质：追加说明，不替换原阶段报告。
科学状态：**REJECTED**。
原全仓保护门禁：**GR0=BLOCKED**。
P13 限定范围恢复门禁：**GR0-S=PASS**，仅放行失败结果交付。

## 1. 原报告与追加证据

原报告：`docs/superpowers/plans/2026-10-28-surrogate-p13-progress-through-t14.md`。
原报告 SHA256：`def21d8f7c6b704c7e04d7b1c34c8535335d65f5dc09bce3bc634f9266004dbe`。
原报告保持原字节。本文件是其追加澄清。

| 证据 | 路径 | SHA256 |
|---|---|---|
| H01 原归属记录 | `ML/backend/surrogate_data/optimization_p13_20261027_r01/resume_20261029_r01/ownership/disposition.json` | `24b8319bd2ae27ea8b85e83228c9673ea8a1776b3665dec45ed0e9e6bdb6c7d0` |
| GR0-S 限定范围方案 | 同目录 `ownership/gr0s_scope.json` | `d83febbf3876c3cf38a050090976bd40e8db7679297bcd4ebc38efd03c0c2317` |
| A4 GR0-S 回执 attempt_002 | 同目录 `verification/gr0s_review_attempt_002.json` | `27851c97051aa4f5cf63b319c0fc0a7b3e9dcc9d6b2e909bd5f050befa365d73` |
| H02 科学冲突审查 | 同目录 `science_review/conflict.json` | `1a988718583f7077c10fece68b92b5299123d54b367856e992f213a332ec9951` |
| H03 数据审查 | 同目录 `data_review/clarifications.json` | `1bc2dd1581540e8df98abc97c0348ce597192ffac7404497ce1388591e2139f1` |
| H04 机器契约 | 同目录 `contracts/failure_delivery_schema.json` | `6149b6bdc4ae78adc302f246c15593b6c7c48692b00d21262fbb233abbeb9d4f` |
| H04 文字契约 | 同目录 `contracts/failure_delivery_contract.md` | `f91c816789e54360472acf4961b958cbf236fb8cf61a8bfa9d0d7d7ec2a004c5` |
| H04 A4 清单 | 同目录 `contracts/artifact_manifest.json` | `0c1815085acda6f40e6ca9c98c4b28408e1514dbbb284e67905ac452dcda2486` |
| H04 A2 交叉核对 | 同目录 `contract_crosscheck/contract_crosscheck.json` | `26912ae7f7afabc22bde0c7d52e64634fd2c65de55c496b1fb91b3d9e2f47320` |
| H04 A2 清单 | 同目录 `contract_crosscheck/artifact_manifest.json` | `d14917652a23024505db334b5c3680016fb0171f3d5b9cf84e4a7ec8e4d938df` |

`ML` 展开为 `/Users/jingbozuo/Projects/aln-data-backend-ml`。
上表路径均以 `ML/backend/surrogate_data/optimization_p13_20261027_r01/resume_20261029_r01` 为根时，省略重复前缀。

## 2. 保护门禁的适用范围

原 H01 和原 A4 回执仍为 `GR0=BLOCKED`。以下八项历史保护差异的删除归属继续记录为 `UNKNOWN`：

- `MAIN/wheel-explore-state.png`；
- `MAIN/docs/WEB-RUNBOOK.md`；
- `MAIN/docs/superpowers/plans/2026-08-18-static-web-platform.md`；
- `MAIN/docs/superpowers/specs/2026-08-18-static-web-platform-design.md`；
- `MAIN/e2e_web.py`；
- `MAIN/nn_demo_1000.csv`；
- `MAIN/scripts/dev-desktop.ps1.bak`；
- `MAIN/start-surrogate.command`。

不推断其由用户清理。不自动恢复、复制替代或更新 O00 来消除差异。

独立 A4 的 GR0-S attempt_002 回执确认：O01 的 138 项输入全部匹配；ML 的 147 项 O00 保护文件全部匹配；184 项登记证据无未解释差异；实际入口、现有导入闭包、工具配置和环境不依赖上述八项。后续写入范围也不涉及上述八项。

因此，GR0-S 只放行 P13 限定范围失败交付。它不构成原 GR0 通过，也不解决全仓历史保护问题。

## 3. R1：绝对目标与保守提升量的判据冲突

原阶段报告未展开该公式冲突。H02 追加以下边界：

- 绝对候选 k² 点预测应与 `15.674470284168734` 比较，单位为百分数数值。
- 保守提升量按原计划应与 `1.0` 个百分点比较。
- O02 的相关文字把绝对目标用于保守候选—基线差值，和原计划的 A 级公式存在冲突。
- O02/O09 保持原字节。本 run 不实现、不启用该 A 级判据。

O09 的合格模型集合为空。外层拟合、新搜索、重采样和邻域预测均未执行。受冲突影响的 A 路径未执行。恢复许可不表示原 G1 的全部公式已重新通过。

若未来需要启用 A 路径、改变科学公式或把坐标观测加入科学输入，必须另立科学协议和新 run。

## 4. R2：P3 原角色与 P13 新增排除层

原报告 §4.1 写有“E1-1 整组保持 `baseline_only`”。追加准确表述如下：

P3 源划分及原角色保持不变。P13 另加 E1-1 设计组排除层，将同组两个结构 `s_d02c967ca1c27ac3`、`s_f7db3a637f7a4b11` 标为 `role=baseline_only`、`baseline_usage=final_difference_only`。

- `structure_holdout` 的原拟合角色为 `train+validation`。有效拟合集由 424 个结构/212 个设计组变为 422/211。
- `fl_upper` 的原拟合角色为 `train`。有效拟合集由 408/204 变为 406/203。
- 两处均排除 2 个结构、1 个设计组 `d_71c5a74d95544080`。
- `area_edges` 与 `eg_upper` 中这两个结构的原角色均为 `test`，不是原拟合成员。其有效拟合集分别保持 384/192 与 388/194。

不得把新增排除层描述为 P3 原角色被改写。

## 5. R3：E1-1 坐标记录的登记范围

原报告 §4.4 将“E1-1 坐标观测缺失”列为保守值不可用原因之一。追加准确边界是：**E1-1 坐标级观测未登记于本轮 O01**，不是项目不存在这些观测。

只读审查文件 `ML/backend/surrogate_data/optimization_p3_20261015/data/records.csv.gz` 的 SHA256 为 `2d0d99458bc496504cd4ff596566cf2d9c4d945c97dae51f6aa2a6ac1fdda948`。其中 `mode=deembedded` 且 `structure_id=s_f7db3a637f7a4b11` 的记录为 50 条，`coord` 去重后为 50 个。

该文件仅列为审查依据。它不改变 O01，不进入本轮科学输入、拟合、预测或重采样。

## 6. T11–T14 的零执行含义

原报告任务表及 §4.4 的计数追加如下解释：

T11–T14 的 PASS 表示失败分支按要求执行完成，不表示相关科学计算通过。没有执行外层拟合、外层预测、新搜索、重采样或候选邻域预测。

外层拟合数、外层预测数、新增网格预测数、EG 扩展执行数、模型重拟合数、bootstrap 预测数、邻域预测数和随机抽取数为 0，是禁行或失败分支的证据。不得解释为这些计算已经成功或通过验收。计划种子没有被使用。

## 7. 历史测试与 G6/G7 的边界

原报告 §5 的旧 `118 passed`、`8 passed` 和各任务日志，只证明对应历史检查在当时的输入和产物上通过。

G6/G7 尚未由本追加文件签收。不得借用旧测试数量签收 G6/G7，也不得据此宣称最终交付回归或页面验收通过。后续 T18/T19 必须保留新的实际命令、日志和退出码。

## 8. 科学拒绝的结论边界

原报告 §1 的科学状态追加如下解释：

`REJECTED` 表示在当前冻结协议、模型集合和已登记证据下没有合格模型。它不能证明所有可能结构均无法达到目标。

该结论不排斥未来另立科学协议、补充证据并开展独立 run。本轮不得借此恢复拟合、搜索、外层预测、bootstrap 或 COMSOL 求解。

## 9. H04 失败交付契约与后续许可边界

H04 将 O14 固定为 20 行历史对照，保持 O13 原顺序。当前结论为 `decision=evidence_insufficient`、`reason=no_eligible_models`。推荐数为 0，A/B/C 数量均为 0，候选等级为 null。

H04 的 15 项合成测试通过。A2 的 52 项独立检查和 33 项拒绝探针通过。A2 未发现实质性科学或实现不一致。该结果只支持 GR1 的软件与契约部分，不改变科学状态。

R05 仅允许后续 T15–T19 按该失败契约执行。不得追加模型、搜索、外层预测、bootstrap 或 COMSOL 求解，不得生成 A/B 推荐。

当前证据不支持 k² 提升 1 个百分点。该句子表示当前证据不足，不表示目标在科学上不可能达到。
