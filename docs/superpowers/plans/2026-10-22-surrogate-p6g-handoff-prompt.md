# P6G → P6H 交接提示词（粘贴给新 agent）

继续 ALN 谐振器代理模型优化的 P6 阶段。先读交接文档 `docs/superpowers/plans/2026-10-22-surrogate-p6g-results.md`（必要时回溯 p6f/p6e 结果文档），然后执行下面的任务。

## 现状一句话

旧 de Excel 的 Qp/Qs/k²/fs/fp 已由当前 `original`/`vz` 校准配对在 125 条记录上精确复现（"Qp 去嵌不可复现"不再是模型偏差的合理解释），但小面积 PF 的 Qp 模型区间仍 0/8 覆盖，偏差原因未定位。

## 任务：小面积 PF 结构/位置关系对照

1. 取 P6E 的 8 个小面积 PF 结构记录，按面积分组（700 vs 900 μm²）。
2. 求两组坐标交集；在交集坐标上逐点比较 Qp（及其他可用目标）。
3. 记录每组各自的 FL、端口、设计组归属和缺失坐标清单。
4. 结论区分两类解释：坐标构成差异 vs 面积趋势。输出为配对描述性比较，不得写"面积导致"类因果表述。

## 数据与工具

- 工作目录：`/Users/jingbozuo/Projects/aln-data-backend-ml/backend`（ML worktree，脚本写这里的 `scripts/`）。
- 记录与旧标签：`surrogate_data/optimization_p6e_20261022/`。
- 去嵌曲线与配对：`surrogate_data/optimization_p6g_20261022/`（audit/ 下 375 条曲线、candidate_results.csv；溯源配对见 p6f 的 candidate_calibration_matches.csv）。
- 原始数据：`/Users/jingbozuo/00.PROJECTS/ALN-DATA/data/#2.zip`。
- 运行方式：`uv run --no-sync`；新脚本过 `ruff check`。

## 禁止事项

- 不修改产品算法、校准匹配规则、任何标签，不训练/校准模型。
- 不按已查看过的 test 集调权重——所有当前测试均已查看，无"新盲测"可用。
- 不做 Git 提交、不部署、不写回主仓库数据目录、不改既有 dirty 文件。
- 不把数值复现当作历史流程来源的证明；Bode Q 微小差异（≈1.2e-5）单独追踪，不与 Qp 问题混同。

## 交付

- 脚本 + receipt/verification（输入及关键代码 SHA256）。
- 结果文档按 P6 系列格式写回主仓库 `docs/superpowers/plans/2026-10-22-surrogate-p6h-results.md`：方法与边界 → 数据 → 判断 → 后续交接 → 复现命令。
