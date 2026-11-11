# 代理模型优化交接：P6G → P12（2026-10-27）

面向零上下文接手者。范围：从 `docs/superpowers/plans/2026-10-22-surrogate-p6g-results.md` 的交接项开始，到 Qp/k² 外推寻优与离线 HTML 展示为止的全部工作。**P6G 交接项已全部闭合；当前瓶颈是实测验证，不是再跑模型。**

## 1. 工作目录与产物布局

- 主仓库（文档、产品代码）：`/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master`
- ML worktree（全部训练/分析脚本与产物）：`/Users/jingbozuo/Projects/aln-data-backend-ml/backend`
- 原始数据：`/Users/jingbozuo/00.PROJECTS/ALN-DATA/data/#2.zip`
- 运行方式：ML worktree 内 `uv run --no-sync`；脚本过 `ruff check`。
- 各轮产物：`surrogate_data/optimization_<轮次>_<日期>/`，含 receipt/verification（输入与脚本 SHA256）。
- 结果文档：主仓库 `docs/superpowers/plans/`，每轮 protocol（运行前冻结）+ results。

## 2. 已完成工作链

| 轮次 | 内容 | 结论 |
|---|---|---|
| P6H | 700 vs 900 μm² 固定坐标交集比较 + 900 组 164 条 original/vz 重提参核验 | 表观 Qp 差距由坐标构成差异主导，非面积趋势；全部 289 条 Qp/Qs/k²/fs/fp 标签被当前代码复现（1e-8）；Bode Q 除外 |
| P6I（`2026-10-26-surrogate-bodeq-trace-results.md`） | Bode Q 复现差异追踪 | 优化器终止级噪声（平坦目标函数×数值环境），非配置/数据精度问题；复现校验容差 ≤4e-5 相对，结案 |
| P7 | 趋势截断/逐目标配方/残差收缩四臂受控实验 | 全未达判据，保留 A0；坐标构成正式排除（敏感性比残差小约一数量级）；失败=700-PF Qp 水平超出特征面 |
| P8 | 实测 fs/fp 频率特征契约（用户授权契约变更） | 主指标 −15.5% 未达 −20%，约束越限，保留 B0；频率在 700 失败位点有帮助、900 反向有害 |
| P9 | 层级标签不确定性（异方差 GP/校准） | 子集覆盖 65.4%→96.2%、宽 1.40×，但点误差 +72% 越限，保留 C0；标签离散度确实携带区间缺失信息 |
| P10 | 仅子集异方差区间 + 仅 Qp 频率子模型 | D1 覆盖达标但宽 4.5× 越限；F1 约束通过（+4.5%）但主指标仍不达标；**推荐配置维持 C0/P6C** |
| D2 显示（`2026-10-26-surrogate-d2-report-display.md`） | D2 异方差区间"仅报告"进 P5 离线页 | 主平台无标量预测/区间链路，故落在 P5 页；默认关、会话级、不进导出 |
| P11 | Qp/k² 联合外推寻优（冻结粗网格） | 无候选点预测/区间下界超基线；候选 A/B 组 + 排除清单 + 证伪方案 |
| P12 | 细网格 9801 点 + 双模型峰搜索 + EG 延长线 | **P1203（EG3.0/FL0.025/5500/PF0）GP k²=15.640% 超基线 14.674%**，但 ExtraTrees 同点 14.454 不同意；Qp 仍无超基线候选 |
| 红点视图（`2026-10-26-surrogate-p11-extrapolation-view.md`） | 外推候选红色菱形进 P5 离线 HTML，点击显示结构参数 | 最新桌面文件 `~/Desktop/AlN结构寻优-P12-外推候选-20261026.html`（16 个候选点） |

## 3. 当前关键科学结论（不要推翻重来，先读原文）

1. 旧 de Excel 标签可由当前 original/vz 配对 + ShortOpen + 当前提参代码数值复现（289/289，Bode Q 按 4e-5 容差）。**数值复现 ≠ 历史流程来源已证实**；default 顺序对照指标不得回填标签。
2. 700-PF Qp 模型区间 0/8 覆盖：已排除去嵌复现、坐标构成、配方、可收缩偏差；失败是 Qp **水平**超出 [EG,FL,area] 特征面。机制未定位。
3. Qp 与 Qs/k²/fp 组间行为解耦（P6H），不可用一个配方/一种偏差统一解释。
4. 仿真（COMSOL）无电学损耗：Qp 无仿真先验；k² 仿真趋势可用但有 P6B 反例。
5. 所有当前测试均已查看，**无新盲测**；任何模型/配方选择只能用训练内证据（组隔离 CV、面积轴伪边界），外层仅回顾性报告，selection 先于外层。
6. 基线：Qp 冠军 C6-1（EG1/FL1.5/5500/PF0，Qp=1395.96、k²=14.014%）；k² 冠军 E1-1（EG2/FL0/5500/PF0，k²=14.674%、Qp=1179.63）；实测 Pareto 前沿 6 点。

## 4. 待办（按优先级）

1. **实测验证（最高优先，需要流片/测量，agent 无法独立完成）**：证伪方案已冻结于 `surrogate_data/optimization_p12_20261026/falsification_plan.md`（P11 亦有初版）。最小集合建议：P1203 首位 + P11 A 组 4 个 + B05/B08；同批复测 C6-1/E1-1 对照；每结构 ≥12 坐标、original/vz 去嵌；通过=典型值落入区间且 Qp 或 k² 至少一项超基线。EG>3 更深外推（fl1 分支 GP 趋势至 15.83，fl0 回落）**排在 EG=3.0 实测验证之后**。
2. **900 内层单元频率特征恶化机理**（fl0 0.0327→0.3562，约 11×）：诊断候选，未立项。
3. **位置 MAD 测量学含义复核**（工艺离散 vs 仪器）：未立项。
4. **新仿真**：P12 仅列出建议仿真点，未跑 COMSOL。
5. **主平台集成（独立立项）**：主平台无标量预测/区间链路；D2 与外推点目前仅在离线 HTML。若需进主平台分析工作区，是先建链路的大功能，不是小改动。
6. **工程收尾**：所有工作无 git commit、无部署。主仓库仅新增文档；代码与产物在 ML worktree 未提交。既有 dirty 文件（ML worktree：`app/core/filename.py`、`app/core/mapping.py`、`scripts/train_surrogate_stage1.py`；主仓库：`frontend/package-lock.json` 等）为历史/用户并行状态，**不要回滚或混入提交**。

## 5. 复现与验证入口

- 各轮重跑命令见对应 results 文档末尾；产物目录含 RESULTS.md 镜像。
- 当前测试基线：ML worktree `uv run --no-sync pytest tests/ml` **118 项全绿**；P5 页构建器 `scripts/build_optimization_p5.py`（可选 `--p10-root` / `--p11-root` / `--p12-root`，不给参时产物与早期构建字节一致，有回归测试）；Playwright 场景 8/8。
- 冻结纪律：P3 划分、P6C 冻结配方与全部对照链不得解冻（用户明确否决划分解冻）；新实验必须先写 protocol 再运行；失败对照与负面结果保留。

## 6. 给接手者的一句话

诊断与模型迭代已做到现有数据的边界：配置仍是 C0/P6C，唯一超基线信号是 P1203 的单模型 k² 外推。**下一步最有价值的动作是按已冻结的证伪方案流片实测，而不是继续调模型。**
