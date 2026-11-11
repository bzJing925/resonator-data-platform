# 代理模型数字交付更正与三维仿真补充：AI 分阶段执行计划

日期：2026-11-04。计划 ID：`POSTSEAL_3D_V1`。

本文是待执行计划，不是已执行证明。用户把本文件交给代理并要求执行后，才开始 D01。

## 0. 目标、旧结论与命名隔离

按顺序完成：报告事实更正 → 数字交付后置签收 → 仿真输入审计 → 有条件的三维仿真研究 → 独立审查与封存。

已知问题 `D08-A4-001`：旧 `delivery_report.md` 把 D02 全阶段写成没有新推理；原执行回执及日志实际记录了 40 次已保存 P3 外折模型推理、3,040 行预测。只有最后一次规范化重算没有推理。未发现重训证据。

用户已明确不会提供新增范围外实测。三维仿真可以补充，但不能替代前瞻实测。本计划不追求把代理模型改成全局可信。

**本文件 D01–D12 是新计划阶段，不是旧 D00–D08 的重跑。** 所有回执必须包含 `plan_id=POSTSEAL_3D_V1`。旧 D09/D10 状态统一使用字段 `legacy_d09`、`legacy_d10`，保持 `NOT_RUN`；本计划 D09 是历史对照评价，不是接收新实测。

固定路径：

```text
MAIN=/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master
ML=/Users/jingbozuo/Projects/aln-data-backend-ml
BE=/Users/jingbozuo/Projects/aln-data-backend-ml/backend
LEGACY=/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_domain_candidates_v1/attempt_001
B_RUN=/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_device_median_v1/attempt_001
NEW_BASE=/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/postseal_3d_v1
PLAN=/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/docs/superpowers/plans/2026-11-04-surrogate-postseal-and-3d-agent-execution-plan.md
```

`RUN` 由 D01 唯一创建为 `NEW_BASE/attempt_NNN`：从 001 起选择第一个不存在的目录，禁止覆盖；协调者公布其绝对路径。其他代理只能消费该路径，不得各自创建 attempt。

执行期间，MAIN、ML 的现有代码、旧计划、P13、B、FL0、整个 LEGACY 只读。新增研究代码和产物全部写入 RUN 中本阶段拥有的目录。不运行旧 fit/finalize/search，不修复项目现有脏文件，不提交、推送、部署或制造。

无论后续仿真结果如何，旧性能候选数保持 0、旧协议保持 `DRAFT_MISSING_INPUTS`、`trusted=false`。两点身份固定为：

- EG 上界诊断：`d05_e8f34862c3e0cefdffeb831c`。
- FL 下界诊断：`d05_0592f0e43ee8a94d5751249b`。

## 1. 回执、独立审查及阶段门禁

### 1.1 两份回执，不能自审

每阶段的实施者只写 `RUN/receipts/Dnn.json`，独立审查子代理只写 `RUN/review/Dnn/**`。实施回执先封存，审查回执随后引用其 SHA256，实施回执不再回填审查哈希。

实施回执最低结构如下。示例占位值必须替换成实际值，未完成的工作不能照抄 PASS：

```json
{
  "plan_id": "POSTSEAL_3D_V1",
  "stage_id": "D01",
  "attempt_id": "attempt_001",
  "status": "PASS",
  "outcome": "COMPLETED",
  "negative_exit": false,
  "author_agent_id": "实际工具返回的代理标识",
  "contributor_agent_ids": ["全部实施参与者的实际代理标识"],
  "started_at": "ISO8601",
  "finished_at": "ISO8601",
  "inputs": [{"path": "绝对路径", "sha256": "实际64位摘要"}],
  "outputs": [{"path": "绝对路径", "bytes": 123, "sha256": "实际64位摘要"}],
  "commands": [{"argv": ["实际命令及参数"], "cwd": "绝对路径", "exit_code": 0, "log_path": "绝对路径或null"}],
  "checks": [{"id": "检查名", "status": "PASS", "evidence": "实际读取/复算/复哈希的范围及结果"}],
  "issues": [],
  "scientific_status": "RETROSPECTIVE_ONLY",
  "next_action": "REQUEST_INDEPENDENT_REVIEW",
  "legacy_d09": "NOT_RUN",
  "legacy_d10": "NOT_RUN",
  "trusted": false
}
```

`status` 仅允许 PASS/FAIL，含义是本阶段执行与交付完整性，不是科学假设成功。`issues` 逐项写 `id/severity/evidence/impact/required_user_input`。无命令的文档步骤使用 `commands=[]` 并说明不适用。

独立审查回执 `RUN/review/Dnn/receipt.json` 必含：

- `plan_id/stage_id/status(PASS|FAIL)`；实际 reviewer ID、被审查 author ID；二者不同。
- 被审查实施回执的绝对路径和 SHA256；实际复核的产物路径和 SHA256。
- `checks`、`findings`、`issues`、实际读取/复算/复哈希范围。
- `negative_exit`：必填布尔值，与实施回执一致，审核者必须验证其来源。
- `released_next_stage`：仅允许下一阶段编号；终止、未决、FAIL 时为 null。
- `legacy_d09=NOT_RUN`、`legacy_d10=NOT_RUN`、`trusted=false`。

审查者必须是另一个真实子代理，其ID不在author或contributor中，未参与本阶段实现或物理参数选择；不得由实施者换角色名称自签。没有可用独立代理就停下来报告，不能模拟审查。审查者不得修改被审查文件，也不得派生代理。

下一阶段实际启动前必须同时满足：实施PASS、审核PASS、released_next_stage正确、即将消费的实施回执和输入/产物摘要与审核记录一致。只读取旧PASS字符串不足以放行。任何依赖内容改变即使对应门禁失效；保留旧记录，停止受影响工作，不能重新计算一个摘要就继续。

**负结果输入解析顺序：** D08–D11先只读取前序实施与独立PASS回执，核对双方的negative_exit。若为true，本阶段只消费这两份回执及其列明的负结果说明，执行动作1、写回执并独立审查；本阶段“输入”中正常路线的冻结参数、评价结果、诊断结果等不要求存在，也不得尝试打开。实施/审核回执持续传递negative_exit=true。只有false才核验并读取全部正常输入；正常路线所需文件缺失属于执行错误，不得改成负结果规避。D01–D06默认negative_exit=false。

### 1.2 状态与退出规则

| 情形 | 实施回执 | 独立审查及后续 |
|---|---|---|
| 任务按计划完成 | PASS / COMPLETED | 审查 PASS 且明确释放下一阶段才继续 |
| 科学不支持、结果为零、预算内不收敛，但证据完整 | PASS / NEGATIVE_RESULT | 审查可 PASS；按本计划的负结果路线收口 |
| 必要用户事实缺失，无法继续 | FAIL / NEEDS_USER_INPUT | 审查确认缺项，不释放下一阶段，报告后结束当前回合 |
| 命令失败达到限额、哈希冲突、越界修改 | FAIL / EXECUTION_ERROR 或 INTEGRITY_FAILURE | 立即停止；独立审查不得替代用户决定 |
| 后台命令还在执行 | 暂不生成最终实施回执 | 只写一次 launch 记录；返回接续说明，不进入下一阶段 |

已封存的实施/审查回执不可覆盖。用户明确授权修复后，在新 `NEW_BASE/attempt_NNN` 中登记前一 attempt 和全部依赖哈希；引用不变的已通过产物，从首个失效阶段重新执行。不得自动创建新 attempt 绕过 FAIL。

授权续接时，仅A0可在新RUN根写一次 `continuation.json`，登记用户授权来源、supersedes_attempt、first_stage_to_rerun，以及每个已接受前序阶段的实施/独立回执绝对路径和SHA256。每项仍需双PASS并复核摘要；任何依赖失效就把first_stage_to_rerun前移并停问用户确认，不自作主张扩大重跑。前序阶段回执不复制、不伪造到新RUN。之后本文件所说“Dnn回执”按当前RUN中实际新回执或continuation的显式前序引用解析；D12清单可引用多个attempt的绝对路径。没有continuation的首次执行必须从D01开始。

清单不包含自身摘要。阶段实施回执不把自身或将来的审核回执列为输出。最后一级审查回执没有更上级摘要是正常的，不为追求循环自证修改已有清单。

## 2. 并行分支、所有权与长命令

这里的“分支”指可分给不同 AI 会话/子代理的任务分支，不要求切换现有 Git 分支。所有新增实现都在 RUN；本计划不创建 Git worktree，不把现有未提交 ML 代码当成已提交基线。用户若另行要求 Git 分支交付，先补写并确认 Git 隔离方案。

| 分支 | 职责 | 阶段写入范围 |
|---|---|---|
| A0 协调者 | 分配 RUN、调度、综合回执、报告和最终封存 | D01、D02、D03、D12阶段目录；D04/D05/D10/D11动作明确指定的汇总文件；各阶段 `receipts/Dnn.json` |
| A1 数据与身份 | 历史数据、结构对应、统计与提参 | D04/data、D05/data、D09、D10/data、D11/data |
| A2 物理与材料 | 版图、材料、物理边界、仿真解释 | D04/physics、D05/physics、D06/physics、D10/physics、D11/physics |
| A3 数值与运行 | 运行环境、模型实现、数值求解 | D04/runtime、D05/runtime、D06/runtime、D07、D08、D10/runtime、D11/runtime |
| A4 独立审查 | 每阶段审核及放行 | 仅 `review/Dnn/**` |

完整写入规则：实施文件写 `RUN/stages/Dnn/` 中分配的子目录；本阶段日志写对应子目录的 `logs/`；每个实施分支可以写自己子目录的 `branch_receipt.json`，A0 只读取并生成综合实施回执，不修改其他分支产物。尚未列出的共享目录不允许写入。

所有权补充：D07/D08的计算、结果和not_run由A3负责，D09由A1负责；D10/D11的汇总决定和上游负结果not_run由A0负责。D06的provenance、适配代码和针对性检查均在runtime子目录，由A3负责。其余未标实施分支的文档汇总动作由A0执行。A0派发前将具体输出文件名登记到任务中，每个文件只有一个作者；不能用“协调者”身份覆盖分支结果。

用户选择数字交付收口时，特许A0只写当前阶段根目录的closure.json及综合实施回执，不取得该阶段其他分支文件的修改权限。

Python命令从BE执行 `uv run --no-sync python ...`，禁止uv sync或安装依赖；设置 `PYTHONDONTWRITEBYTECODE=1`、`GIT_OPTIONAL_LOCKS=0`。本阶段生成器、检查脚本、COMSOL模型和运行目录使用绝对路径。需要临时目录时只使用本阶段目录下新建的tmp；不得改HOME或CODEX_HOME。缺依赖即停报。

**阶段串行、阶段内并行。** D04、D05、D06、D10、D11 可按表并行；所有同阶段分支结束、综合回执封存、独立审查 PASS 后，才可进入下一阶段。最多四个活跃代理：A0 + A1/A2/A3；审核时先结束实施子代理，再启动 A4。不同阶段可使用不同的 A4 实例。

长任务规则：

1. 预计超过20秒的命令在唯一 tmux 会话启动；命令、cwd、输入哈希、输出/日志/退出码路径写 `<本分支拥有目录>/logs/jobs/<command_key>/attempt_01/launch.json`。stdout/stderr与退出码放同一目录，唯一允许的机械修正写attempt_02，禁止覆盖attempt_01。command_key取D05冻结工况ID与计算目的；没有新工况授权不得新增key绕过失败上限。阶段回执登记所有launch文件摘要。
2. 启动命令必须把 stdout/stderr、退出码写到当前阶段日志目录；没有 tmux 或必要执行接口则停报，不安装替代环境。
3. 单次启动后，继续本阶段与该结果无依赖的工作；禁止派另一个代理替自己轮询。
4. 使用运行工具的完成事件接续；若没有完成事件且无独立工作，向用户报告一次会话名、日志路径和接续入口，然后结束当前回合。用户确认任务结束或明确要求检查时只读一次退出码。
5. 任务仍未结束，不重复读取日志、状态、进程或退出码，不循环 sleep，不自动重启。
6. 一个阶段默认同一时刻只运行一个 COMSOL 求解进程；D05 只有在许可和内存证据支持时才可冻结更多并发槽位。并行文档工作不占求解槽位。

## 3. 分阶段执行

### D01：建立隔离目录和冻结输入清单
- 输入：本文 PLAN；MAIN/AGENTS.md、MAIN/CLAUDE.md；LEGACY 的 delivery_report.md、README.md、final_report.json、final_manifest.json、receipts/D08.json；原2026-11-03计划。
- 动作：
  1. 读取上述文件；只读记录 MAIN/ML 的分支、HEAD、`git status --short`，将已有修改和删除记录为基线，不恢复或清理。
  2. 创建唯一 RUN；写 `stages/D01/context.json`，登记路径、计划ID、角色、写入范围、无新增范围外实测这一用户约束。
  3. 按旧 final_manifest 的20个产物及8份有效审核记录逐项重算字节数和SHA256；再核对旧 receipts/D08.json中的三个封存输出，去重后应为29个文件。记录实际数量，出现数量变化也停止说明，不能更新预期值。
  4. 核对有效D03/D05使用二次审核回执，D00–D07均PASS且next_task依次释放；将证据写 `stages/D01/legacy_integrity.json`。
  5. 写 `stages/D01/legacy_state.json`：原性能候选0、诊断点2、协议草案、原D09/D10未运行、trusted=false；对PLAN和当前delivery_report也登记哈希。
  6. 写实施回执，随后交另一子代理独立审查；审查完成前不启动D02。
- 完成标准：目录没有覆盖；29个封存文件全部匹配；有效审核链无缺失；路径及状态记录完整；未知变更为0。任一完整性异常为FAIL并停报。
- 回执：`RUN/receipts/D01.json`，产出路径及SHA256包括context、legacy_integrity、legacy_state；遗留问题写issues。独立回执 `RUN/review/D01/receipt.json`，仅PASS可释放D02。

### D02：生成报告更正版，不修改旧封存
- 输入：D01实施与独立审查PASS回执；LEGACY/delivery_report.md、receipts/D02.json、review/D02/receipt.json、comparison/execution_summary.json、logs/D02_execution_retry_1.log。
- 动作：
  1. 核对总P3调用数40、预测3,040行、拟合次数0，以及最终重算模式CANONICALIZATION_RECOMPUTE_NO_INFERENCE；证据冲突立即停报。
  2. 写 `stages/D02/erratum.md`，标记D08-A4-001、错误句、原始证据路径/字段/行号及更正原因。此前聊天审查只可写成用户提供的审查背景，不能伪造已落盘独立回执。
  3. 从旧报告生成 `stages/D02/delivery_report_corrected.md`，仅增加版本/适用范围说明，并将错误句改成：“D02未拟合或重拟合；使用已保存且训练身份兼容的P3外折模型完成40次推理，生成3,040行预测；随后规范化重算复用这些预测，没有再次调用模型推理。”其他事实和数字不变。
  4. 写 `stages/D02/report.diff` 及 `revision_manifest.json`；清单包含旧报告摘要、新报告摘要、更正说明和diff，不含自身。
  5. 对旧报告重新哈希，要求与D01一致；写实施回执，交另一子代理独立审查。
- 完成标准：事实错误已在新版本纠正；diff只含指定更正与版本说明；旧报告和原D08三份封存产物不变。不得用“新模型”一词掩盖实际发生的已保存模型推理。
- 回执：`RUN/receipts/D02.json`，登记四个新文件路径及SHA256和遗留问题；独立回执 `RUN/review/D02/receipt.json`，仅PASS释放D03。

### D03：完成数字交付后置签收包
- 输入：D01–D02实施及独立审查PASS回执；更正版报告；LEGACY中的候选、域策略、实测协议及final_report。
- 动作：
  1. 只读核对：性能候选0、最高逐点k²改善0.2301781867207513个百分点、两个诊断点身份、1,224个诊断范围和0个性能允许范围。
  2. 写 `stages/D03/digital_acceptance.md`，说明更正已获独立复核，签收仅针对数字产物；列明实测协议未就绪、无前瞻实测及模型可信声明。
  3. 写 `stages/D03/digital_manifest.json`，引用旧封存、新更正、D01/D02实施与独立回执；不修改原D08状态，不把尚未产生的本阶段审查写成PASS。
  4. 写实施回执，交另一子代理审查报告更正关闭和数字签收边界。审查者给出 `legacy_delivery_acceptance=PASS|FAIL`；原封存状态仍不变。
- 完成标准：唯一报告缺陷关闭，补充清单可追溯，无新缺陷；只放行数字交付。此后仿真缺输入不撤销该数字签收。
- 回执：`RUN/receipts/D03.json`，登记签收说明、清单路径及SHA256、遗留问题；`RUN/review/D03/receipt.json`仅PASS释放D04。

### D04：并行盘点数据、物理输入和运行资源
- 输入：D03实施及独立审查PASS；LEGACY/fabrication_inputs.json、search_protocol.json、shortlist.csv、measurement/measurement_protocol_v2.json；B_RUN/splits.json、data/、models/；用户已经提供的实际仿真资料。
- 动作：
  1. A1写 `stages/D04/data/inventory.json`：历史结构、两诊断点参考结构、可追溯原始复数曲线、提参入口、raw/de参考面和校准来源。只读检索MAIN、ML和用户明确给出的目录，不扫描整台电脑或访问外部账户。
  2. A2写 `stages/D04/physics/inputs.json`：实际几何/版图、材料层厚/晶向、EG/FL/AG/PF定义、端口映射、腔体或反射层、支撑与边界、损耗参数、容差；每项记录值、单位、来源及CONFIRMED/ASSUMED/MISSING，不从面积猜电极轮廓。
  3. A3写 `stages/D04/runtime/environment.json`：COMSOL实际安装路径、版本、许可模块信息的可核实来源、可用调用方式、内存和CPU；仅运行帮助/版本等不求解的命令，不安装软件、不占用远端付费资源。
  4. 各分支写自己的branch_receipt；A0汇总 `stages/D04/readiness.json`。关键几何、堆叠、参考面、原始曲线或可用求解环境缺失，登记NEEDS_USER_INPUT；损耗缺失单列Qp资格缺口。
  5. 将全部未决事实合并成 `stages/D04/questions.md`，每项说明阻塞哪个目标和需要哪个文件/数值；不得自行认定用户不要继续仿真。
  6. 写综合实施回执，交另一子代理独立审查。缺输入即FAIL，向用户只报告一次缺项并停止；输入确实齐全才继续。
- 完成标准：三个分支盘点完整且无越界；进入D05所需输入均有证据。缺资料的盘点文件可以完整，但不能据此给本阶段放行PASS；用户选择数字阶段结束时按第4节正常收口。
- 回执：`RUN/receipts/D04.json`包含全部分支产物及SHA256、逐项缺口；`RUN/review/D04/receipt.json`只有在输入完整时PASS并释放D05，否则released_next_stage=null。

### D05：冻结点集、物理假设、统计规则和计算预算
- 输入：D04实施及独立审查PASS；全部CONFIRMED输入；旧两诊断点及其源支撑结构；用户对D04缺项的可追溯答复（如有）。
- 动作：
  1. A1生成 `stages/D05/data/point_registry.csv`：两个固定诊断点、每点同端口/同映射/同切片的已测边界参考、E1-1，以及历史校准/评价结构。全结构键匹配，角色互斥；诊断点不进入校准。B1-6/A1-6若使用只标历史对照，不标未测几何。
  2. A1冻结校准/保留评价结构及design_group；同一设计组不得跨集合。具体数量和选点若无已确认方案，先写方案和问题后停问，不能按预测优劣挑选。保留评价称“回顾性”，不称新盲测。
  3. A2写 `stages/D05/physics/model_spec.json`：逐层几何、物理接口、边界、端口、材料/损耗、可调参数及有来源的范围、允许的对称简化、模态识别规则；不确定项不得填默认值。
  4. A3写 `stages/D05/runtime/run_budget.json`：确切调用argv模板、求解接口、网格三个等级、频率采样序列、域/吸收边界检查、最大求解次数、每次时间/内存、总预算、求解并发数和完成通知方法。网格/频率加密是不同的预注册工况，不是失败命令重试。
  5. A0形成 `stages/D05/simulation_protocol.json`，统一冻结提参定义、复数曲线误差公式、历史逐点评价门槛、校准算法和停止准则、参数敏感性情景列表、数值误差预算、每项合格目标。建议的k²数值变化≤0.025个百分点、Qp≤2%必须明确为数值收敛预算，不是实测准确度；频率、曲线和模态阈值须有具体数值。
  6. 将用户事实及未由本文确定的科学/资源决策写 `decision_register.json`，每项附用户答复或已有受认可协议的路径。未确定样本分配、损耗处理、校准参数边界、阈值或预算时写FAIL并停问；禁止自行选择优化器、容差、统计分布或算力预算。
  7. 明确Qp定量资格：若无法建立可核实的损耗与评价口径，必须取得用户明确选择“仅k²/机制路线”的记录，再冻结 `qp_quantitative=false`；不能自动降级后继续。
  8. 写综合实施回执，交另一子代理独立审查；全部冻结后才允许首次求解。
- 完成标准：协议所有执行字段有具体值且可追溯；没有TBD/UNKNOWN进入执行字段；点集、校准与评价集合、有限预算及失败出口完整；审查PASS。
- 回执：`RUN/receipts/D05.json`登记协议、点集、三个分支配置、决策记录及SHA256；`RUN/review/D05/receipt.json`仅PASS释放D06。

### D06：实现一个参考结构的三维模型与提参入口
- 输入：D05实施及独立审查PASS；冻结model_spec、simulation_protocol、run_budget；确认的COMSOL与后端本地环境。
- 动作：
  1. A2仅按冻结模型说明生成几何/材料/边界映射说明，写 `stages/D06/physics/model_mapping.json`；缺失映射不得临时假设。
  2. A3在 `stages/D06/runtime/` 实现最小模型文件和可重复运行入口，全部缓存、日志、临时求解文件、导出写本目录；模型不得保存回来源文件。
  3. 提参优先只读复用现有纯函数；新增最小适配仅做单位、端口、参考面及文件格式对齐，不改业务代码或旧算法。将源代码哈希和复用入口记录在provenance.json。
  4. 运行D05批准的一个参考工况，导出频率、复数Y/Z或S、fs/fp、k²、许可的Q指标、模态与能量输出。不能从四个标量反造复数曲线。
  5. 做一次针对性检查，覆盖单位/端口、全结构身份、频率顺序、有限值和提参口径；检查脚本保存为本阶段产物，明确不代表收敛或器件验证。
  6. 写实施回执，交另一子代理核对模型与冻结说明和导出契约；不进行参数校准。
- 完成标准：参考工况可运行、所有输出可追溯、没有临时物理假设或旧代码修改。运行错误按两次上限处理；不能运行时FAIL停报。
- 回执：`RUN/receipts/D06.json`登记模型、代码、映射、参考结果、日志、检查及SHA256；`RUN/review/D06/receipt.json`仅PASS释放D07。

### D07：执行数值收敛检查
- 输入：D06实施及独立审查PASS；D05冻结网格、频率、域边界工况和收敛判据。
- 动作：
  1. 在 `stages/D07/` 使用D06模型的工作副本，运行全部预注册收敛工况；不改材料参数或增补未登记工况。
  2. 对相同目标模态计算fs/fp、k²、许可Q指标、复数响应及能量的变化，按冻结规则逐项判断。
  3. 写 `convergence.csv`、`convergence_decision.json` 和运行清单；保留失败/非收敛工况，不只保留最佳网格。
  4. 预算内得到稳定解且指标达到预算时标NUMERICALLY_CONVERGED；工况正常完成但判据不通过时标NEGATIVE_RESULT，并设 `negative_exit=true`；求解器异常导致证据无法完成则FAIL。
  5. 写实施回执，交另一子代理独立复算收敛判据。负结果也必须审查，不能伪装成计算完成即物理可信。
- 完成标准：预注册工况齐全或异常被明确记录，判据计算正确。PASS/NEGATIVE_RESULT后D08–D11只执行各自负结果接续动作；FAIL立即停报。
- 回执：`RUN/receipts/D07.json`登记收敛数据、结论、工作模型及SHA256、遗留问题；`RUN/review/D07/receipt.json`若执行证据完整可PASS并释放D08，同时保留negative_exit。

### D08：只使用校准集确定仿真参数
- 输入：D07实施及独立审查PASS；D05校准集、参数范围、算法和预算；D07选定数值设置。
- 动作：
  1. 若上游negative_exit=true，仅写 `stages/D08/not_run.json`，注明数值检查负结果导致校准未运行，继承negative_exit；不加载校准数据做优化。
  2. 否则将冻结算法应用于校准集，禁止访问保留评价集目标值用于参数选择；不得优化代理模型权重或修改历史标签。
  3. 写 `calibration_results.csv`、`frozen_parameters.json`、`calibration_decision.json`；记录每次参数尝试、目标值、边界及停止原因。算法返回不满足判据的稳定结果为合法负结果，不另选算法。
  4. 预算或校准判据不满足时设置negative_exit=true；执行错误则FAIL停报。
  5. 写实施回执，交另一子代理检查数据隔离、范围、预算和参数冻结；不得由审查者替实施者调参。
- 完成标准：正常路线参数已冻结且仅使用校准集；负结果路线具有可追溯not_run或失败科学结论。不存在看评价结果回调参数的行为。
- 回执：`RUN/receipts/D08.json`记录实际存在的输出及SHA256、遗留问题；`RUN/review/D08/receipt.json`仅执行证据审查PASS释放本计划D09。

### D09：完成历史保留结构评价
- 输入：本计划D08实施及独立审查PASS；D05保留评价集及逐目标判据；D08冻结参数。
- 动作：
  1. 若negative_exit=true，仅写 `stages/D09/not_run.json` 并继承原因，不开展评价求解。
  2. 否则在不修改模型和参数的前提下计算全部保留结构；使用冻结参考面和提参版本，与历史测量逐点对照。
  3. A1写 `evaluation.csv`，包括每结构每目标误差、复数曲线差、分支、方向、适用口径和是否达标；不得用总体平均掩盖必需结构失败。
  4. 写 `evaluation_decision.json`：k²和Qp资格分别判定；仅D05已批准k²/机制路线时可不评价Qp。任一必需目标失败，设置negative_exit=true，不看结果后删除目标或结构。
  5. 写实施回执，交另一子代理独立复算误差和判定。明确本阶段是历史回顾性评价，legacy_d09、legacy_d10仍NOT_RUN。
- 完成标准：全部冻结评价点均有结果或明确失败记录；无校准泄漏或事后改阈值。科学失败可以PASS/NEGATIVE_RESULT进入收口路线。
- 回执：`RUN/receipts/D09.json`登记评价表、决定或not_run及SHA256；`RUN/review/D09/receipt.json`仅审查PASS释放D10。

### D10：计算两个固定诊断点及对应参考
- 输入：D09实施及独立审查PASS；D05点集；D08冻结参数；旧两诊断点冻结预测。
- 动作：
  1. 若negative_exit=true，仅写 `stages/D10/not_run.json`，不计算诊断点。
  2. 否则A3在runtime目录按预算计算两个固定诊断点和各自参考；EG/FL结果存不同子目录，只有D05允许多个求解槽位才同时运行。
  3. A1并行整理旧冻结预测和参考实测的身份/单位/数据来源，写data目录；A2并行检查冻结的物理解释模板，写physics目录。两者不得预填尚未生成的仿真结果。
  4. 计算结束后A1对齐结果，写 `stages/D10/data/diagnostic_results.csv`，分别列代理预测、三维仿真、历史实测；差值和比值使用登记的同口径参考，E1-1不可比时记NOT_COMPARABLE，不更换弱基准。
  5. A2检查每点模态和能量解释，写 `stages/D10/physics/interpretation.json`；出现模型与仿真分歧完整保留，不平均二者。A0读取两个分支文件后写 `stages/D10/diagnostic_decision.json`；本阶段不新增性能候选。
  6. 写综合实施回执，交另一子代理独立检查身份、参考、冻结参数及结论边界。
- 完成标准：仅固定两点及已登记参考；曲线/标量来源明确；无新搜索、调参或性能推荐。正常仿真结果不支持预期趋势为合法负结果。
- 回执：`RUN/receipts/D10.json`包含分支输出、结果或not_run的路径及SHA256；`RUN/review/D10/receipt.json`仅PASS释放D11。

### D11：执行冻结敏感性情景并界定结论
- 输入：D10实施及独立审查PASS；D05有限参数情景和预算；D08冻结参数；D10诊断结果。
- 动作：
  1. 若上游negative_exit=true，仅写 `stages/D11/not_run.json`，不得补算以挽救负结果。
  2. 否则A3仅执行D05已冻结情景；不临时增加材料、厚度、损耗扫描或随机种子。参考点和诊断点采用一致情景。
  3. A1计算每点相对对应参考的变化范围、符号变化及排序变化，写data目录；没有概率分布依据就只叫情景范围，不生成95%预测区间。
  4. A2对照数值收敛误差、历史匹配偏差和输入敏感性分别解释，写physics目录，不合并成无依据的总置信度。
  5. 写 `sensitivity_decision.json`，仅选择SIMULATION_SUPPORTS_DIRECTION、SIMULATION_CONFLICT、INCONCLUSIVE之一并标明具体结构/轴/距离/假设；不改变旧trusted或性能候选。
  6. 写综合实施回执，交另一子代理独立复算范围及结论。即使所有情景均有利，也不得写前瞻验证或制造就绪。
- 完成标准：情景列表没有增删；全部结果保留；结论严格限于冻结假设。趋势冲突或不稳定是合法结局，不启动再训练。
- 回执：`RUN/receipts/D11.json`登记情景结果、判定或not_run及SHA256；`RUN/review/D11/receipt.json`仅PASS释放D12。

### D12：汇总并封存最终补充研究包
- 输入：D01–D11实施与独立审查PASS回执；所有实际产生的产物；负结果接续记录（如有）。
- 动作：
  1. 写 `stages/D12/final_report.md`，分开报告数字交付签收、仿真输入完整性、数值收敛、历史评价、两点诊断、敏感性和未执行项目。
  2. 写 `final_status.json`，终点仅选DIGITAL_AND_SIMULATION_COMPLETE或DIGITAL_COMPLETE_SIMULATION_NEGATIVE；旧性能候选0、旧协议草案、legacy_d09/d10 NOT_RUN、trusted=false固定列出。当前研究与旧状态不得共用无前缀字段造成混淆。
  3. 对D01登记的29个原封存文件及旧delivery_report再次复哈希，写 `legacy_preservation.json`；任何变化立即FAIL停报，不自行恢复原件。
  4. 写 `final_manifest.json`，列全部实际交付文件（含legacy_preservation）及D01–D11的实施/审查回执路径、字节数和SHA256；排除清单自身、D12实施回执和后续审查。大文件按实际文件哈希，不用文件名代替。
  5. 写D12实施回执，登记final_manifest等摘要；随后交另一子代理做最终独立审查，审查回执引用D12实施回执摘要，禁止回写清单。
  6. 最终审查PASS后只输出一次用户交付说明：产物绝对路径、实际终点、科学结论、未运行内容和限制；结束本轮，不自动开启新搜索。
- 完成标准：全部引用可追溯、无哈希循环、旧封存未变、所有负结果与未运行记录明确；最终独立审查PASS。PASS只表示该补充包完整可信地报告了工作，不表示实测外推有效。
- 回执：`RUN/receipts/D12.json`记录最终文件及SHA256、遗留问题；`RUN/review/D12/receipt.json`必须released_next_stage=null。

## 4. 正常结束、停报与接续

### 正常结束（负结果也是合法结局）

1. **完整路线：** D12及独立审查PASS，按真实结果交付仿真支持、冲突或不确定结论。
2. **科学负结果路线：** D07/D08/D09产生完整、经审核的负结果，后续阶段依序仅写not_run并独立审查，最终D12封存。不得跳过阶段假造完成，也不得强行运行被禁止的后续计算。
3. **用户明确选择只完成数字交付：** D03已PASS，用户确认不补资料或不做仿真后，在当前阶段目录写 `closure.json` 和本阶段实施回执（若本阶段已封存FAIL，使用经用户授权的新attempt），由另一子代理独立审查；`outcome=USER_ACCEPTED_DIGITAL_ONLY`、后续未开始阶段在closure中统一NOT_RUN、released_next_stage=null。不伪造后续阶段回执。

### 必须立即停下来报告

- 旧封存、计划或已冻结输入哈希不符；源文件被并行改写；出现未解释的数据身份/端口/映射冲突。
- 必要材料、几何、损耗处理、参考面、统计阈值、样本划分、许可或预算缺失；计划未规定的判断。
- 同一逻辑命令连续失败2次；独立审查FAIL；命令实际超出预算；需要增加依赖、改旧代码或新增搜索。
- 不能提供真实独立审查者，或不能按不轮询规则完成后台运行接续。

停报格式只包含：阶段ID、已完成项、失败/缺项证据、受影响产物、需要用户回答的最小问题。保存当前阶段FAIL回执及可得日志；若哈希异常，禁止继续消费有冲突的输入。不要修改前序结果或开新attempt重试。

技术命令第一次失败后，只允许一次能够从错误直接确定、且不改变物理/统计/数据契约的机械修正，例如已证实的本阶段路径拼写或参数名错误；仍须记录原错误。没有这种确定修正，第一次就停报。第二次失败无条件停止。更换参数、脚本文件名、代理或会话不能重置计数。

## 5. 禁止事项

- 不许重复打印无变化状态；只在阶段完成、首次阻塞、重要新证据或后台启动/完成时报告。
- 不许轮询等待，不许循环读取日志/进程/退出码，不许sleep循环，不许把轮询委托给子代理。
- 不许同一逻辑命令连续失败2次后继续执行或变名重试。
- 不许修改非本阶段负责的文件；不许修改其他分支产物、前序回执或审查者证据。
- 不许在上一阶段独立审查PASS之前启动下一阶段；不许实施者自签独立PASS。
- 不许修改LEGACY、P13、B、FL0、旧模型/标签/协议，或清理已有dirty worktree。
- 不许自行重训、扩大候选点集、加密搜索网格、追种子、放松门槛、删失败点或换弱基准。
- 不许将历史间距当制造精度，将未知AG/材料/厚度补成默认值。
- 不许安装依赖、制造、流片、外部实验、部署、联网发送项目材料或启用付费远端算力。
- 不许把B所选方法与P6C计为独立双模型；不许把树模型范围外平坦输出当趋势证明。
- 不许把历史P90、敏感性情景范围或数值收敛误差冒充预测区间。
- 不许把复数仿真曲线当实测曲线，或把它当成原四标量模型已通过复阻抗物理验证。
- 不许把任务完成、负结果完整或独立审查PASS改写成性能提升、可制造、可部署、前瞻实测有效或trusted=true。
- 不许为文档改动运行构建、全量测试或lint；实现完成后只做一次相关针对性检查，失败按上述两次上限。独立审查的必要复算/复哈希不冒称模型重跑。

## 6. 每个分支的可复制提示词

### 分支A0：协调执行

```text
请执行以下计划，不要重新设计计划：
/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/docs/superpowers/plans/2026-11-04-surrogate-postseal-and-3d-agent-execution-plan.md

你的角色是A0协调者。先读取计划和当前AGENTS.md/CLAUDE.md，从D01开始。仅你能创建RUN和发布综合receipts/Dnn.json。公布RUN绝对路径后，再将同阶段独立任务分给A1/A2/A3。阶段必须顺序执行；所有分支完成且另一真实子代理A4审查PASS后才进入下一阶段。
最多四个活跃代理；先结束实施代理再启动独立审查。A4不能参与被审阶段实现。旧D00-D08封存只读，本计划ID是POSTSEAL_3D_V1；legacy_d09/d10始终NOT_RUN、trusted=false。无新增范围外实测。
遵守阶段目录所有权；不修改旧代码或其他分支产物。缺输入、未写判断、审核FAIL、同一逻辑命令连续失败2次时停下来报告。禁止轮询、重复状态和自动追加搜索。每个PASS必须能由实际文件和SHA256证明。最终只交付计划指定产物。
```

### 分支A1：数据、身份和评价

```text
先读上述计划的完整绝对路径：
/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/docs/superpowers/plans/2026-11-04-surrogate-postseal-and-3d-agent-execution-plan.md

你是A1数据分支。只接受A0给出的RUN绝对路径、当前阶段编号、前序独立PASS回执路径及本阶段任务。缺任一项就停止并报告，不自行分配RUN或推进阶段。
只执行所有权表中该阶段分配给A1的动作，只写自己的data目录；本计划D09允许写其评价产物。登记实际读取/复算范围及产物SHA256，写branch_receipt.json后把路径交A0。不得写综合阶段回执或独立审查回执。
必须用完整结构身份、端口和mapping_semantics；分开raw/de、历史测量、代理预测和仿真。校准/保留评价按设计组隔离，不把历史数据称新盲测，不改标签、基准或候选。计划未写的选择停问。不得派生代理，不轮询等待，不修改其他分支文件。
```

### 分支A2：物理输入和解释

```text
先读计划：
/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/docs/superpowers/plans/2026-11-04-surrogate-postseal-and-3d-agent-execution-plan.md

你是A2物理分支。仅使用A0提供的RUN、当前阶段及前序独立PASS回执。只执行当前阶段A2动作，只写stages/Dnn/physics和本目录branch_receipt.json。
逐项追溯实际版图、材料、层厚、晶向、EG/FL/AG/PF、边界、端口和损耗。未知项列问题，不从面积猜轮廓、不把历史间距当加工精度、不默认材料参数。D05未冻结的物理判断必须停问。
解释只针对已完成、已登记的仿真和具体结构/轴/距离；模型与仿真冲突必须保留，仿真曲线不等于实测。不得改数据、代码、前序协议或其他分支产物；不得自签独立审查，不派生代理，不运行制造或外部实验。
```

### 分支A3：数值实现和运行

```text
先读计划：
/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/docs/superpowers/plans/2026-11-04-surrogate-postseal-and-3d-agent-execution-plan.md

你是A3数值分支。A0必须提供RUN、当前阶段、前序独立PASS回执及本阶段输入哈希。只写所有权表授权目录，只读复用现有代码。D05未PASS前禁止求解。
只实现冻结模型、算法、点集、情景和预算；环境或许可缺失停报，不安装依赖。所有缓存、求解文件、日志、退出码写本阶段目录。长命令用唯一tmux和launch.json，禁止轮询；无完成通知且无独立工作时报告一次并结束回合等待用户接续。
同一逻辑命令第二次失败立即停。不要加网格搜索、调材料解释结果、换优化器或动旧fit/finalize。写branch_receipt和实际SHA256交A0，不写独立审核回执，不派生代理。
```

### 分支A4：每阶段独立审查

```text
先读计划：
/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/docs/superpowers/plans/2026-11-04-surrogate-postseal-and-3d-agent-execution-plan.md

你是独立A4，不得参与当前被审阶段实现。A0必须提供RUN、当前阶段ID、已封存实施回执路径及SHA256、前序独立回执。缺项就FAIL，不自行补写实施材料。
逐条核对当前阶段完成标准、依赖PASS链、写入范围、执行命令、文件哈希和科学边界。需要复算时独立从原始数据计算，明确本次读取/复算/复哈希数量，不把历史审查当本次已执行。科学负结果可被接受，但缺输入、执行不完整、哈希不符或结论越界不能放行。
只写RUN/review/Dnn/**；生成receipt.json，记录真实reviewer ID与author ID、实施回执哈希、证据和缺陷。PASS时只释放计划允许的下一阶段；终止或FAIL时released_next_stage=null。不要修改实施产物、运行重训或派生代理。不得放宽门槛或因希望继续而给PASS。
```

A0向各分支派发时，必须追加下列真实值，不能把占位符直接交给子代理执行：

```text
PLAN_ID=POSTSEAL_3D_V1
RUN=<D01实际创建的绝对路径>
STAGE=<当前Dnn>
PREVIOUS_REVIEW=<前序独立PASS回执绝对路径；D01为NONE>
PREVIOUS_REVIEW_SHA256=<实际SHA256；D01为NONE>
OWNED_DIRECTORY=<本阶段该分支唯一写目录>
TASK=<从当前阶段动作中逐条复制的任务>
```

用户如手动开启多个AI会话，也使用这些提示词和同一RUN；A0先发布本阶段任务再启动分支。工作分支交付后停止，不能自行接下一个阶段。现有工具最多四并发时，不能同时开A0+A1+A2+A3+A4五个活跃代理。

## 7. 参考与适用范围

- 原执行计划：`MAIN/docs/superpowers/plans/2026-11-03-surrogate-domain-candidates-and-prospective-validation-plan.md`。
- 当前事实以LEGACY原始回执、日志、CSV及实际复哈希为准，不以聊天概述代替。
- COMSOL官方模型说明仅用于确认可用物理建模路径，不能代替本项目材料和几何：[Thin-Film BAW Composite Resonator](https://doc.comsol.com/6.4/doc/com.comsol.help.models.mems.thin_film_baw_resonator/thin_film_baw_resonator.html)。
- 数值收敛检查依据：[Performing a Mesh Refinement Study](https://www.comsol.com/support/knowledgebase/1261)。数值收敛不代表物理假设或实测外推已验证。

## 给执行者的总指令

从 D01 开始顺序执行，每步只做本计划写明的事；每阶段先完成实施回执，再由另一个真实子代理独立审查。只有审查 PASS 且明确释放下一阶段才允许继续。多分支只在同一阶段内并行，严格遵守文件所有权。计划没写的判断一律停下来问用户，不以常识、默认值或“合理推断”补齐材料、物理、统计、预算或权限。负结果也是合法结局；缺输入不是自行重试的理由。禁止重复打印状态、轮询等待、连续两次失败后再试和修改非本阶段负责文件。保留原封存与全部失败证据，legacy_d09/d10始终NOT_RUN、trusted始终false；达到本计划规定终点后交付一次并结束。
