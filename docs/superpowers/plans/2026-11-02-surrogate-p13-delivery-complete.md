# P13 负结果交付完成总结（阶段 A）

日期：2026-11-02。
阶段：A00–A07 全部完成。
交付状态：**delivery_status=PASS**。
科学状态：**scientific_status=REJECTED**。
推荐数：0；`eligible_models=[]`；`trusted_for_unmeasured_optimization=false`。

## 1. 结论

P13 负结果交付已正式封存。该结论只表示交付链、页面、数值复现、软件门禁和清单完整，不表示 k² 提升目标达成。

**当前证据不支持 k² 提升 1 个百分点。**

原全仓门禁保持 `GR0=BLOCKED`。本次使用的是经归属签收后的限定范围恢复权限，不改变八项 MAIN 历史 UNKNOWN 删除的未解决状态。

## 2. 门禁与修复结果

| 任务 | 结果 | 关键证据 |
|---|---|---|
| A00 | PASS | 当前快照、保护差异和写入范围；独立复核通过 |
| A01 | PASS | FL0 归属及 3,360 个输出文件独立核验；AGENTS 用户归属登记 |
| A02 | PASS attempt_002 | 1,148/1,148 既有引用备份；旧 attempt/旧回执/正式 O15 写入防护 |
| A03 | PASS attempt_002 | ALN_VIEW 正式测试链复现 490 个 JSON null 缺陷；18 个真实反例 |
| A04 | PASS | 490 处违规归零；其他 99 个空字符串语义保持；候选二次构建逐字节一致 |
| A05 | PASS | 新 V18 全量软件验收 |
| A06 | PASS | 独立 G7 13,724/13,724 项通过，2,474 项哈希记录无差异 |
| A07 | PASS | 9 个正式文件原子提升；独立读回 93/93 项通过 |

A05 实际结果：

- Ruff：23 个文件，0 错误；
- 完整 ML 回归：265 passed，0 failed/skipped；
- Node：165/165；
- Playwright：11/11，其中原 7 个场景和新增 4 个空值/控制场景；
- 浏览器断言：62/62，其中原 44 项和新增 18 项；
- O14/O15 一致性：38/38；
- 390px 页面无横向溢出，外部请求为 0；
- 1,434 个保护文件无未解释差异。

## 3. 归属登记

- m00433：用户确认 `MAIN/AGENTS.md` 与 `MAIN/AGENTS.md.bak` 为用户修改/创建或用户认可的其他任务产物，保留并登记。
- m00465：用户确认 `backend/scripts/report_fl0_qp_zh_20261102.py` 和 `backend/surrogate_data/optimization_fl0_audit_20261102/无框架器件异常阻抗曲线与数据清洗说明.html` 为用户本人创建，保留并登记。
- FL0 原四个脚本、后续脚本、审计输出和桌面报告均保持只读，未作为 P13 写入目标。
- 未恢复、删除、移动或重新设定八项历史 UNKNOWN 删除的基线。

## 4. JSON 空值修复

旧 O15 的 490 个契约违规已全部修复：

- 20 个候选 × 5 个保守/邻域字段 = 100 个 JSON `null`；
- 78 个缺失指标行 × 5 个缺失来源字段 = 390 个 JSON `null`。

CSV 仍使用空字段表示缺失。其他 99 个空字符串按 H04 逐字段保留：17 个 `exclusion_reason` 和 82 个有值行的 `null_reason`。有效数值 0、完整精度字符串、20 个候选顺序、160 个指标行、82 个有值行、78 个缺失行及 C19 来源语义均未改变。

## 5. 正式交付文件

根目录：`ML/backend/surrogate_data/optimization_p13_20261027_r01/`。

- `viewer/index.html`
- `viewer/browser_data.json`
- `viewer/t16_artifact_manifest.json`
- `verification/test_report.json`
- `verification/t18_artifact_manifest.json`
- `verification/final_report.json`
- `verification/t19_artifact_manifest.json`
- `delivery/README.md`
- `delivery/final_artifact_manifest.json`

正式读回：`ML/backend/surrogate_data/optimization_p13_20261027_r01/resume_20261029_r01/recovery_20261102/attempt_001/A07/formal_readback.json`。
读回结果：PASS；`delivery_status=PASS`；`next_task=B00`。

所有候选、历史失败 attempt、A02 备份、V17/V18 日志、G7 回执和提升映射均保留。正式清单无自哈希和循环引用。

## 6. 使用与复现

- 直接用浏览器离线打开 `viewer/index.html`，无需服务器或 CDN。
- 页面导入使用同目录 `viewer/browser_data.json`；篡改或不合规输入不会替换当前有效状态。
- 数值复现和完整软件验收入口、命令、日志、环境及哈希见正式 V17/V18 和各 attempt 清单。
- 不使用 MAIN 旧启动脚本或旧 Web 测试脚本。

## 7. 限制

没有新增流片、COMSOL 求解、模型训练、外层预测、新搜索、bootstrap 或候选邻域预测。没有独立新结构实测。交付 PASS 不应解释为实测性能提升，也不自动授权候选推荐。

阶段 B 必须另建独立数据与运行目录，不将“全部器件中位数”标签或新模型结果写回 P13。
