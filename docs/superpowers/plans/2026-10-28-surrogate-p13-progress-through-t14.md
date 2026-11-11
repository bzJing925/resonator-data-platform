# P13 进度报告：T00–T14

日期：2026-10-28。
暂停点：T14 已完成。T15 尚未启动。
本报告不是最终交付报告。G6 和 G7 尚未执行。

## 1. 当前结论

科学状态为 **REJECTED**。
没有 M1 配置同时满足三项接受条件。
M2 与 P3 的统计口径不一致。M2 只作旁证。
O09 的 `eligible_models` 为空。
本轮已转入失败结果交付路径。

没有执行新增流片或 COMSOL 求解。
没有执行新增推荐搜索。
没有执行外层拟合或外层预测。
没有执行重采样拟合或候选邻域预测。
没有候选获得 A 或 B 等级。
最终候选表、独立页面和最终交付包尚未生成。

## 2. 路径与冻结标识

- MAIN：`/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master`
- ML：`/Users/jingbozuo/Projects/aln-data-backend-ml`
- RUN：`/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p13_20261027_r01`
- O02：`RUN/contracts/protocol.json`
- O02 SHA256：`bc0f16a4791d1968cac10afafd5fd14a32abfa7bd7d0329b2591ff825d88f05c`
- O09：`RUN/selection/selection.json`
- O09 SHA256：`ca034c07d87db0f9980b75b195f0d1ac44fc7e77662f1f03c153a9f6540e1de4`

E1-1 的实测典型 k² 为 `14.674470284168734%`。
目标为 `15.674470284168734%`。
目标增加量为 1 个百分点。
程序使用完整精度值。程序不使用页面舍入值作判定。

## 3. 任务状态

下表分开记录执行状态和科学状态。
执行 PASS 不表示性能通过。

| 任务 | 执行状态 | 科学状态或结果 | 主要证据 |
|---|---|---|---|
| T00 | PASS | G0 通过 | `control/preflight.json` |
| T01 | PASS | 冻结 138 个输入 | `audit/input_manifest.json` |
| T02 | PASS | 完成 P1203 峰审计 | `audit/peak_audit.json` |
| T03 | PASS | REJECTED；软约束 DISABLED | `audit/simulation_gate.json` |
| T04 | PASS | 折构造通过；13 个容量键不足 | `evaluation/splits.csv`、`evaluation/capacity.csv` |
| T05 | PASS | G1 通过；协议冻结 | `contracts/protocol.json` |
| T06 | PASS | G2 通过；契约交叉核对通过 | `verification/contract_report.json` |
| T07 | PASS | 完成 M1 训练内预测 | `m1/inner_predictions.csv` |
| T08 | PASS | REJECTED；M2 只作旁证 | `m2/inner_predictions.csv`、`m2/aggregation_audit.csv` |
| T09 | PASS | 无 M1 配置通过三项条件 | `verification/model_gate.json` |
| T10 | PASS | REJECTED；空合格模型集合冻结 | `selection/eligible_models.json` |
| T11 | PASS | REJECTED；不执行外层拟合 | `evaluation/retrospective_report.json` |
| T12 | PASS | REJECTED；不执行新增搜索 | `search/search_protocol.json` |
| T13 | PASS | REJECTED；保留失败对照 | `search/shortlist.csv` |
| T14 | PASS | REJECTED；稳健性证据不可用 | `robustness/robustness_report.json` |

T15–T19 均为 **NOT_STARTED**。
当前没有 RUNNING 任务。
当前没有技术性 BLOCKED 事项。
用户指令要求在此暂停。

## 4. 主要科学证据

### 4.1 输入与划分

O01 冻结了 138 个输入。
输入没有缺失项。
输入没有未解释差异。

P12 当前制品包含 20 行。
其中 17 行为非负面对照。
其中 3 行为负面对照。
当前 HTML 显示 17 个候选。
旧文档中的 16 个候选漏列了 P1206。
O01 已登记该版本差异。旧文档未修改。

O05 包含 30,528 条划分记录。
O05 包含 53 个有效折单元。
O05 包含 59 个无效折单元。
A2 独立复算未发现设计组泄漏。
E1-1 整组保持 `baseline_only`。

20 个硬容量键中有 13 个不足。
门槛保持为至少 20 个独立设计组和至少 2 个有效边界单元。
本轮没有降低门槛。

### 4.2 P1203 峰审计

P1203 的 P6C GP 预测为 `15.639782124032903%`。
该值比目标低 `0.034688160135831` 个百分点。
P3 在同点的预测为 `14.45401787067623%`。
P3 不支持该点的达标声明。
局部九点邻域中没有点达到目标。
该结果不支持 k² 提升 1 个百分点的声明。

### 4.3 模型评价

M1 完成了 12 个冻结配置。
每个配置包含 2,882 条训练内评价预测。
M1 共有 34,584 条预测。
无效预测为 0 条。

M2 完成了 10 个冻结配置。
每个配置包含 2,882 条训练内评价预测。
M2 共有 28,820 条预测。
无效频率和无效 native k² 均为 0 条。
这些数值检查不改变 M2 的科学拒绝状态。

P6C 对照包含 2,882 条相同评价行。
两份独立对照的 actual 差值为 0。
两份对照的预测最大绝对差为 `1.2434497875801753e-14`。
该差值在冻结容差内。

P6C 的普通 MAE 为 `0.06376968945489789` 个百分点。
P6C 的边界 MAE 为 `0.240019072487491` 个百分点。
P6C 的最差单元 MAE 为 `0.6473449040546779` 个百分点。

没有 M1 配置同时满足以下条件：

1. 边界 MAE 至少降低 10%。
2. 普通 MAE 恶化不超过 5%。
3. 最差有效单元 MAE 恶化不超过 5%。

M2 的统计口径与 P3 不同。
P3 使用逐记录 k² 的层级中位数。
M2 使用结构级预测频率计算 native k²。
M2 不进入合格模型集合。
M2 不能使候选获得 A 或 B 等级。

### 4.4 搜索与稳健性

T11 的外层拟合数为 0。
T11 的外层预测数为 0。
T12 的新增网格预测数为 0。
T12 的 EG 扩展执行次数为 0。

T13 保留了 17 个非负面对照。
这些对照不与 576 个实测结构精确重合。
T13 也保留了 3 个历史负面对照。
C19 与一个实测结构重合。
该记录及其排除原因均已保留。

T14 的模型重拟合数为 0。
T14 的 bootstrap 预测数为 0。
T14 的邻域预测数为 0。
T14 的随机抽取数为 0。
计划种子没有被使用。

`candidate_scores.csv` 保留全部 20 行。
保守值、误差余量和外推距离均为 null。
每个 null 都有原因。
没有用数值 0 代替 null。

计算保守值不可用。
原因包括无合格模型、M2 口径不符、E1-1 坐标观测缺失和容量不足。

## 5. 验证证据

T00 实际运行了当前基线测试。
`uv run --no-sync pytest tests/ml` 的结果为 **118 passed**。
既有页面场景的结果为 **8 passed**。
这些结果属于本轮基线。
它们不代表 G6 已通过。

主要后续验证如下：

| 范围 | 实际结果 |
|---|---|
| T01 输入审计 | 5 passed；O01 独立复核 PASS |
| T02 峰审计 | 10 passed；287 行剖面复核 PASS |
| T03 仿真门禁 | 6 passed；软件验证 PASS |
| T04 划分与修复后回归 | 10 passed；30,528 行独立复算 PASS |
| T06 公共契约 | 48 passed；T04 回归 10 passed |
| A2 契约交叉核对 | 31/31 PASS |
| A3 契约交叉核对 | 42/42 PASS |
| T07 M1 | 8 passed；34,584 行契约验证 PASS |
| T08 M2 | 9 passed；28,820 行及聚合审计验证 PASS |
| T09 模型门禁 | 10 passed；22 个配置独立复算完成 |
| T12 搜索禁行 | 8 passed；20 行原值保持 |
| T13 失败对照 | 7 passed；实测查重完成 |
| T14 不可用证据 | 7 passed；null 和零执行检查通过 |

本轮各任务的 Ruff 检查均登记为通过。
该结论只覆盖各任务列出的文件。
它不是全仓 Ruff 结论。

完整 ML 回归尚未在最终交付物上重跑。
新页面端到端测试尚未执行。
G6 和 G7 尚未执行。

## 6. 偏差、修复与保留事项

### 环境

`/usr/bin/git` 受 Xcode 许可状态影响。
本轮只读 Git 命令改用 `/opt/homebrew/bin/git`。
没有执行提权操作。

一次计数命令在 ML 根目录误建了 `.venv`。
该目录已移入 `RUN/failures/T00/attempt_001/`。
没有在该环境安装依赖。
后续命令均使用 `backend/.venv`。

### T04 复验入口

T04 复验脚本曾未创建新 attempt 目录。
A4 增加了父目录创建和回归测试。
修复后独立复算通过。
O05 科学 CSV 没有变化。

O05 哈希保持为：

- `splits.csv`：`fd000bd582f01286d6d862d1dddffa16199146e7acf07802ab2d8a27050a71a9`
- `capacity.csv`：`77cd9e9b664fa2e0e39a6ebd32c50579ee407adb13cd938d2450e6951896d60f`

### T06 契约

首轮交叉核对发现 11 个实现问题。
A4 修复了这些问题。
修复内容包括普通 MAE 权重、完整行校验和 M2 频率记录校验。
A2/A3 随后完成正式 PASS 核对。
失败证据已保留。

### 文件保护

O00 登记了 MAIN 的 75 个既有 dirty 文件。
O00 登记了 ML 的 147 个既有 dirty 文件。
各任务清单记录了输入和保护哈希。
目前没有发现未解释的旧文件变化。
最终全量保护核验仍属于 G7。

没有提交、推送或部署。
没有安装或升级依赖。
旧模型、旧候选和旧页面未覆盖。

## 7. 暂停后的任务

以下任务尚未启动：

- **T15**：生成 O14 失败结果交付。不得添加 A/B 推荐。
- **T16**：构建 O15 独立页面。页面必须使用 O14 数值。
- **T17**：复算交付数值并检查旧文件哈希。
- **T18**：执行最终 ML 回归和页面端到端测试。完成 G6。
- **T19**：封存 O16、限制说明和最终清单。完成 G7。

恢复时必须从 T15 开始。
恢复时必须沿用 O09 的空合格模型集合。
不得追加模型、软约束消融或第二次 EG 扩展。
不得把科学 REJECTED 改写成性能 PASS。
