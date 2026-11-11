# P13 限定范围恢复执行总结（G7 阻塞暂停）

日期：2026-11-02。
状态：按用户要求完成本阶段收尾并暂停。
科学状态：**REJECTED**。
执行状态：T18 已通过；T19/G7 未通过，正式交付未完成。
原全仓门禁：**GR0=BLOCKED**。
历史限定范围门禁：**GR0-S=PASS**，只适用于 P13 失败交付。

## 1. 本阶段完成的任务

### 1.1 GR0-S 与恢复门禁

- 保留原 H01 和原 A4 回执的 `GR0=BLOCKED`，未把八项 MAIN 历史删除归为已解释。
- 新增 R01 `attempt_002` 限定范围方案，未改写 O00/H01。
- 独立 A4 的 GR0-S `attempt_002` 回执为 PASS：138/138 项 O01 输入、147/147 项 ML O00 保护文件、184 项登记证据匹配。
- R04 完成 H04 失败交付契约；15 项合成测试与 A2 的 52 项独立检查通过。
- R05 完成 GR1/GR2；追加澄清文件为 `docs/superpowers/plans/2026-10-29-surrogate-p13-review-clarifications.md`。

### 1.2 O14、O15 与数值复现

- O14 已生成并通过 GD：20 个历史对照，保持 17 个非负面对照加 3 个负面对照的原顺序。
- `metrics.csv` 为 160 行；82 行有登记来源值，78 行保留缺失原因。
- C19 保持负面对照和实测几何匹配 `s_c454ac7ec8a2a7f8`，并与历史模型预测分开。
- T17 隔离重建与 O14 一致，差异数为 0；数值容差为 `atol=rtol=1e-12`。
- O15 独立离线页面已生成，但其 JSON 缺失值表示仍存在下述契约缺陷，不能视为最终验收交付。

### 1.3 T18 软件门禁

T18 `attempt_001` 曾发现指标选择器、390px 窄屏溢出和一个核验脚本的 Ruff 问题。失败证据已保留。A5/A4 修复后，`attempt_002` 全部通过：

| 检查 | 实际结果 |
|---|---|
| Ruff | 19 个文件，0 错误 |
| 完整 ML 回归 | 265 passed，0 failed/skipped；2 个非关键 warning |
| 严格 TypeScript | 退出码 0 |
| Node 契约测试 | 25 passed |
| Playwright | 7 passed，0 failed/skipped |
| 独立浏览器证据 | 44/44，通过且无外部请求 |
| O14/O15 数值一致性 | 25/25 |
| 390px 布局 | clientWidth=scrollWidth=bodyScrollWidth=390 |

V18：`ML/backend/surrogate_data/optimization_p13_20261027_r01/verification/test_report.json`。
V18 SHA256：`a3871e13d17d6600b861edf7d9835bd4ea9d8edc20a88986462ee865db3f72e7`。

这些结果只证明当时页面及软件检查通过，不覆盖 G7 新发现的 JSON 语义问题，也不表示科学性能通过。

## 2. T19/G7 当前结论

独立 A4 的 G7 `attempt_001` 为 **FAILED**。共执行 1,673 项检查，1,661 项通过、12 项失败；323 项声明哈希无差异，候选自检 701/701 和 Ruff 均通过。

G7 回执：`ML/backend/surrogate_data/optimization_p13_20261027_r01/resume_20261029_r01/verification/final_manifest_check.json`。
回执 SHA256：`a37204ce098a49732efe12384ff54a8ccd4abb9c9ad8b72db1eea52b1ff44524`。

### 2.1 O15 JSON 缺失值契约缺陷

`viewer/browser_data.json` 当前 SHA256：`3ac64441f81aff274d9411a96aa7bb3a30ce0f7c90740b5774e66732e6ee0184`。

H04 要求 JSON 缺失值及缺失来源字段使用 `null`，只有 CSV 使用空字段。当前 O15 却有 490 个字段实例使用空字符串：

- 20 个候选的 5 个保守/邻域字段，共 100 个实例；
- 78 个缺失指标行的 5 个来源字段，共 390 个实例。

这不是哈希差异，而是语义契约不合格。需要由 A5 修复页面数据生成与校验，不得用手工替换正式 JSON 的方式绕过生成链。

### 2.2 新的未登记保护差异

G7 发现新的未登记文件：

`ML/backend/scripts/audit_fl0_qp_20261102.py`

当前 SHA256：`4a969781bbda8c5ba2f19f0904b92309d34b19abb606408bd5aa59e1409fac8e`。

该文件不在 P13 写入范围，未找到可验证的用户或其他任务归属回执。其内容属于另一项 FL=0 曲线审计，但内容和日期不能替代归属证据。

按恢复计划，出现新的未解释保护差异时必须停止后续交付写入。未删除、移动、覆盖或格式化该文件，也未更新 O00 来消除差异。

## 3. 正式交付状态

以下正式 O16 文件均未生成或提升：

- `RUN/verification/final_report.json`；
- `RUN/delivery/README.md`；
- `RUN/delivery/final_artifact_manifest.json`。

T19 候选文件只位于 `RUN/verification/t19_work/attempt_001/candidate/`，不是正式交付。

因此：

- `delivery_status` 不能写为 PASS；
- G7 未通过；
- 推荐数仍为 0；
- 没有 A/B 推荐；
- `trusted_for_unmeasured_optimization=false`；
- **当前证据不支持 k² 提升 1 个百分点**。

没有新增流片、COMSOL 求解、模型训练、外层预测、新搜索、bootstrap 或邻域预测。没有提交、推送、部署或安装/升级依赖。

## 4. 恢复所需步骤

1. 由文件所有者提供 `backend/scripts/audit_fl0_qp_20261102.py` 的具体归属回执；在此之前保持保护阻塞，不删除或覆盖该文件。
2. 归属签收后，由 A5 修复 O15 的 490 个 JSON 缺失值实例，并增加能检测该问题的生成/导入契约测试。
3. O15 改变后重新执行受影响的 T18 页面与软件门禁；O14 不变，不重跑科学实验。
4. 重新生成引用新 O15 哈希的 T19 候选件，加强 G7 自检中的 JSON null 语义检查。
5. 独立 A4 重新执行 G7；只有全部通过，A0 才按字节提升正式 O16。

恢复时不得把原 GR0 写成 PASS，不得改变 O02/O09，不得借本失败交付路径恢复寻优实验。

## 5. 路径缩写

- `ML`：`/Users/jingbozuo/Projects/aln-data-backend-ml`
- `RUN`：`ML/backend/surrogate_data/optimization_p13_20261027_r01`
- `REC`：`RUN/resume_20261029_r01`

本文件是暂停时的追加诊断与总结，不是最终验收报告。
