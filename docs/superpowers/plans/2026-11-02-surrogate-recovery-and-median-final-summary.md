# P13 交付修复与全部器件中位数训练：A/B/C 总收尾报告

日期：2026-11-02。

## 最终状态

| 阶段 | 执行/交付 | 科学状态 | 结论 |
|---|---|---|---|
| A：P13 负结果交付 | PASS | REJECTED | 正式 O16 已封存；推荐数 0 |
| B：全部器件中位数训练链 | PASS | RETROSPECTIVE_ONLY | 冻结研究门槛通过；研究包 trusted=false |
| C：独立新结构实测 | NOT_RUN | 无独立新实测 | 仅交付协议草案；未制造、未测量 |

**当前证据不能声称新结构 k² 提升 1 个百分点。** 阶段 B 的 PASS 是历史数据回顾性研究门槛结果，不是新盲测、生产部署资格或实测性能保证。

## 阶段 A 证据

- O15 的 490 个 JSON 空值契约缺陷全部修复；其他 99 个空字符串按 H04 保留。
- 独立 G7：13,724/13,724 项通过，2,474 项哈希记录无差异。
- 9 个正式 O15/V18/O16 文件原子提升并逐字节读回，93/93 项通过。
- 原 GR0 保持 BLOCKED；FL0 和用户认可的 AGENTS 变更均按归属保留。
- P13 科学状态保持 REJECTED，eligible_models=[]，recommendation_count=0，trusted=false。

总结：`docs/superpowers/plans/2026-11-02-surrogate-p13-delivery-complete.md`。
正式读回：`ML/backend/surrogate_data/optimization_p13_20261027_r01/resume_20261029_r01/recovery_20261102/attempt_001/A07/formal_readback.json`。

## 阶段 B 证据

### 数据与聚合

- 源记录 80,618 条；主数据集为 26,480 个 deembedded、geometry_eligible 器件，覆盖 576 个结构。
- 器件身份覆盖全部源记录；精确重复、真实重复测量和未解决身份冲突均为 0。
- 每个结构、每个参数均按全部器件逐参数计算中位数，未按坐标中位数二次聚合。
- 59 条 FL0 待复核记录保留在主集并标记；敏感性只屏蔽这些记录的 Qp。
- 敏感性改变 30 个结构的 Qp 中位数，其他参数不变。
- 与旧坐标聚合标签相比，5,184 个结构—参数结果变化数为 0。

### 模型与回顾性验证

- 完整 ML 回归：285 passed。
- 固定预算：720 次拟合、40 个内层选择；没有追加网格、种子、bootstrap 或外层调参。
- 冻结研究门槛 PASS：
  - 普通留出 k² MAE 降低 97.164%；Qp MAE 降低 90.872%。
  - 所有必需外层范围的 k²/Qp 均不劣于同折常数基线。
  - 所有必需范围的最小 MAE 降低幅度为 67.9577%。
- Qs/BodeQ 独立报告，20 个范围—目标单元无相对常数基线恶化。
- 700-PF 难点子集保留并单独报告。
- 研究包含 4 个目标模型、24 个分支模型；固定输入回读及不支持输入拒绝检查通过。

最终产物：

- `ML/backend/surrogate_data/optimization_device_median_v1/attempt_001/README.md`
- `ML/backend/surrogate_data/optimization_device_median_v1/attempt_001/final_report.json`
- `ML/backend/surrogate_data/optimization_device_median_v1/attempt_001/final_manifest.json`
- `ML/backend/surrogate_data/optimization_device_median_v1/attempt_001/research_bundle/`
- `ML/backend/surrogate_data/optimization_device_median_v1/attempt_001/B07/review/receipt.json`

B07 独立验收：PASS；362 个清单路径和 15 条复现命令核验完成，无未解释保护差异。

## 阶段 C：独立实测协议

协议草案：`ML/backend/surrogate_data/optimization_device_median_v1/attempt_001/independent_measurement_protocol.json`。

协议冻结了结构/模型版本/预测与评价规则、同批基准、功率/IFBW/频率步长/校准/去嵌记录、重复测量的不确定度处理和全部器件中位数目标。

当前没有制造或独立测量输入，因此状态只能为 NOT_RUN。后续若执行，需要外部提供实际器件与测量记录；不得把历史重新划分当作新盲测，也不得把重复测量当作更多独立器件。

## 收尾边界

- 没有新增流片、COMSOL 求解、候选搜索、部署或生产模型替换。
- 没有安装或升级依赖，没有提交或推送。
- P13 冻结协议、原始科学输入、FL0 产物及旧模型均保持只读。
- 四个研究目标是独立标量预测，不是某一真实器件，也不是自洽的完整阻抗曲线。
- 历史去嵌配方未独立确认，继续作为限制保留。
