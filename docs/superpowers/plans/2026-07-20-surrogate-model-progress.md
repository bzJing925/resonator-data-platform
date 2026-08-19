# 代理模型项目进度与迁移恢复手册

> 最后更新：2026-08-09（会话因迁移机器中断）。本文档是恢复工作的唯一入口。
> 规格：`docs/superpowers/specs/2026-07-20-surrogate-model-design.md`（v4）
> 计划：`docs/superpowers/plans/2026-07-20-surrogate-model.md`（与实现同步）

## 1. 项目一句话

训练两阶段代理模型：输入 5 个结构参数（EG、FL、AG=1.0 占位、面积、PF=massloading 有无）→ Stage 1 基准锚定残差 MLP 预测 7 个性能参数 + 256 稀疏阻抗点 → Stage 2 Transformer 重建 1001 点 Z11(dB) 全谱。训练数据来自桌面版批次 `#2`（~2,900 条抽样，576 结构组合 / ~288 物理器件组）。

## 2. 分支与工作区

| 分支 | 位置 | 内容 |
|---|---|---|
| `main` | `/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master` | aln-data2 已同步；规格 + 计划 + 本文档 |
| `feat/backend-ml-surrogate` | `/Users/jingbozuo/Projects/aln-data-backend-ml`（git worktree） | 全部实现代码（13 个提交） |
| `feat/backend-ml` | （无 worktree） | **旧的未合并 mason-sim 工作（23 提交），勿动** |
| `backup/pre-aln-data2-sync` | — | aln-data2 覆盖前的备份点 |

注意：CLAUDE.md 说 ML 工作应在 `feat/backend-ml` 分支/worktree，但该分支被 mason-sim 占用，故实际用 `feat/backend-ml-surrogate`。

## 3. 任务状态（计划共 15 任务 + 最终审查）

| 任务 | 状态 | 提交（feat/backend-ml-surrogate） |
|---|---|---|
| 0 环境准备 | ✅ | f1e0091（torch+sklearn 依赖） |
| 1 AlgorithmConfig 常量 | ✅ | d18ab91 + 7107e76（小写化） |
| 2 确定性稀疏位置规则 | ✅ | 19491ad + 631d857（默认值接 config） |
| 3 基准表 | ✅ | 6a38a35 |
| 3.5 基准表 PF 维 + 新常量 | ✅ | a1686f0 + 65faf0b（缺档守卫） |
| 4 提取纯函数 extract.py | ✅ | 986e361 + efaab56（freq 轴对齐/单次读取/MAD 趋势面） |
| 5 提取脚本 + npz 落地 | ✅ | e1b32ea + b56d3d9 + 8783b20（众数频率轴/缺档预检/dropped 瘦身） |
| 6 SurrogateDataset | ✅ | 9b7328c + d199bd2（接缝 smoke/精确断言/with np.load） |
| 7 Stage 1 模型 | ✅ | 370efa2（in_dim=5；计划测试夹具 4 维笔误已修正） |
| 8 Stage 1 训练脚本 | ✅ | 1be4f98 |
| 9 B3 ExtraTrees 基线 | ✅ | c61a47a |
| 10 Stage 2 训练脚本 | ✅ | 7959785 |
| 11 端到端推理器 | ✅ | ac16c97 |
| 12 推理 CLI | ✅ | 6f19150（+matplotlib 依赖） |
| 13 评估脚本 | ✅ | 2b6b329（热图网格动态尺寸/npz 物化/fp 重提取补报） |
| 14 真实数据运行验收 | ✅（含结构修复迭代） | 实测结果见规格 §8.1；训练产物仅在 worktree 本地（gitignored） |
| 最终代码审查 | ✅ | 8b7324e（Step A max_freq 半修复/CLI stage2 选项/默认超参对齐） |

## 8. 最终状态（2026-08-10 收官）

- 分支 `feat/backend-ml-surrogate` 全部完成（22 提交），45 项 ml 测试 + ruff 通过
- 实测：全谱 RMSE 逐器件 1.20 dB / 格均值口径 0.93 dB / 位移对齐口径 0.72 dB；
  **逐器件 0.5 dB 目标被证不可达**（格内工艺离散地板 0.79 dB，详见规格 §8.1）
- 级联胜 B0/B2；fs MAE 11.4 MHz 不及 B3 的 9.46 MHz（地板 5.2 MHz）
- 训练产物（不进 git）：`backend/surrogate_data/`（2,840 条 npz+meta+baseline）、
  `backend/checkpoints_surrogate/`（stage1.pt、stage2.pt=Step B 微调版、b3、config/history）
- 关键修复（48f21c3）：SparseReconstructor max_freq=10 使位置编码失效（注意力均匀化），
  改 0.1 + cond/z 归一化后残差通路才工作
- 交互式探索器（8858061）：`cd backend && uv run python scripts/surrogate_viewer.py`
  → http://localhost:8600 ，逐个输入 5 参数实时出谱 + 指标 + 同格真值叠加
- **平台集成**（b37873f + 8a90f22 + 78942a0）：平台新页『代理模型』（/surrogate），
  API `/api/surrogate/status|predict`（懒单例 + LRU 缓存，未命中 20–80ms），
  产物经 `SURROGATE_DATA_DIR`/`SURROGATE_CKPT_DIR` 配置（缺时页面降级提示）；
  部署到桌面版/服务器时把 surrogate_data + checkpoints_surrogate 放到 backend 同级
  或设环境变量；4 项 API 测试 + Playwright 页面验证通过
| 最终代码审查 | ⬜ | — |

## 4. 在新机器上恢复

```bash
# 1. 还原仓库（bundle 含全部分支与历史）
git clone aln-data-migration.bundle aln-data-master
cd aln-data-master

# 2. 还原 Claude 技能框架（计划/执行流程依赖）
tar xzf claude-skills.tar.gz   # 解出 .claude/ 到仓库根

# 3. 重建 ML worktree 与 Python 环境
git worktree add /Users/jingbozuo/Projects/aln-data-backend-ml feat/backend-ml-surrogate
cd /Users/jingbozuo/Projects/aln-data-backend-ml/backend
uv sync --no-install-package zipfile-deflate64
# zipfile-deflate64 在新 macOS SDK 源码编译必失败（内置旧 zlib 冲突）；
# 若新机器无现成编译产物可移植，则：uv pip install --python .venv zipfile-deflate64 试预编译轮，
# 或临时从 pyproject 移除该依赖（仅影响含 deflate64 压缩的 zip 上传，ML 管线不需要）
uv run python -c "import torch, skrf, sklearn; print(torch.backends.mps.is_available())"

# 4. 桌面版数据（不在 git 内，需单独拷贝）
#    ~/.aln-data/aln-data.db + ~/.aln-data/files/（任务 14 真实数据提取的输入）

# 5. 恢复工作流：对 Claude Code 说
#    "按 docs/superpowers/plans/2026-07-20-surrogate-model-progress.md 继续，
#     用 subagent-driven-development 从任务 6 规格审查恢复"
```

## 5. 恢复后的第一步（任务 6 收尾）

任务 6（SurrogateDataset，提交 9b7328c）实现已完成且 30 测试通过，但**规格合规审查被中断**。恢复后：
1. 对 9b7328c 做规格审查（要点：`meta["groups"]` split 过滤、基准与目标同统计归一化、3 个测试 16/4/4 计数）——计划中任务 6 全文在 `docs/superpowers/plans/2026-07-20-surrogate-model.md`
2. 规格通过后做质量审查，再进入任务 7

## 6. 关键技术决策（细节见规格 v4）

- 输入 5 维；AG 当前恒 1.0 占位（数据全空）；PF 来自 `mapping_entries.has_pf`（INNER JOIN，空 mark 约 1,200 条剔除）
- 每结构组合 ≤5 条（|fs−格中位数| 升序）；物理器件（S11/S22 同文件）绑定划分防泄漏
- 基准表 (pf, area) 24 档，eg=0/fl=0 组固定 train；归一化统计只用 train 组
- 清洗顺序 cell→trend→physical；趋势面 MAD 居中比较（std 在 <10 键自掩蔽，Monte Carlo 实证）
- Stage 2 从头训练（原 308nm checkpoint 已在 aln-data2 覆盖时删除）
- 成功标准：全谱 RMSE ≤ 0.5 dB（test 组）；Stage 1 必须显著优于 B3 ExtraTrees 基线

## 7. 迁移文件清单

| 文件 | 内容 |
|---|---|
| `aln-data-migration.bundle` | git 全分支完整历史（main + feat/backend-ml-surrogate + backup 等） |
| `claude-skills.tar.gz` | 仓库 `.claude/`（superpowers 技能框架 + 设置；被 .gitignore 不入库） |
| `~/.aln-data/`（单独拷贝） | 桌面版数据库 + S 参数文件（任务 14 输入；28MB db + files/） |

## 9. 仿真补充实验（2026-08-19，负结果，细节见规格 §8.2）

COMSOL D1/D2 仿真补充实测的完整实验已收官，**结论为负：不更换生产 checkpoint**。

- 可行性（2026-08-17）：仿真 100% 覆盖实测 568 格 + 63 个中间水平新格（L_AG=1.0 过滤后）；域差系统性可校准（ExtraTrees Δ，纯 train 格防泄漏，CV fs 9.9 / fp 13.9 MHz）；Q 不可用（+485%）
- 混合训练：实测 2,391 + 63 校准伪标签格（权重 0.3，pmask 屏蔽 Q 系）
- 多种子消融（3 种子，scripts/hybrid_ablation.py）：test fs baseline 13.80±2.78 / hybrid63 13.74±1.37 / hybrid43 14.28±1.05（全在种子噪声内）；**留出 20 仿真格公平对比 fs 11.42（baseline）→ 14.19（hybrid43），缝隙填充无收益**
- 副产物（正）：纯实测模型在缝隙格上对校准伪标签 fs MAE 11.4 MHz ≈ 校准器噪声地板 → 实测网格够密，COMSOL 独立验证了缝隙插值可信
- 注意：种子彩票实测达 ±3.4 MHz，任何单跑对比（如 11.41 vs 12.90）无意义，评估必须多种子
- 基础设施保留可复用：sim_offset_analysis.py / build_sim_supplement.py / train_surrogate_stage1.py --sim-npz/--sim-weight/--seed / hybrid_ablation.py；产物 backend/sim_offset_report/、backend/ckpt_ablation/（gitignored）

## 10. 仿真参考清洗 + Zp 校正（2026-08-19，Zp 峰高 3.24→0.62 dB，细节见规格 §8.3）

针对"fp 峰拟合差、小面积差几个 dB"的问题，按用户处方完成仿真参考清洗并收官：

- **诊断**：峰高欠预测（bias −2.34 dB）源于晶圆离散平均 + 基准锚被压 7–13 dB；瓶颈在 Stage 1 稀疏头顶点（3.54 dB），稀疏表征本身无损（0.11 dB）；结构→Zp 对 MLP 是模态交叉不连续（3.35 dB 饱和），ExtraTrees 0.62 dB
- **清洗**：每格按校准仿真距离（|Δfs|+|Δfp|+|ΔZp|，MAD 归一）选器件；基准表改用仿真选中 basic 器件；校准器纯 train 格防泄漏。格内 Zp std 1.58→0.47 dB，Stage 1 val 0.117→0.070
- **Zp 校正器**：ExtraTrees→Zp + 推理侧 σ=12MHz 窄高斯峰区校正（infer.py 自动探测 zp_corrector.joblib）
- **最终指标（test）**：Zp 峰高 MAE 0.62 dB（小面积 0.45–0.78）、atfp 0.80、fs 11.05 / fp 12.43 MHz、全谱三项持平旧版
- **Step B 提速 20×**：`--device cpu`（MPS 对该模型慢 7×）+ 训练态点子采样；35 min/次
- 生产 stack 已切换（旧版 `*_prev` 目录）；重打包桌面版需按使用说明 §1.3 刷新产物后重建
