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
| 14 真实数据运行验收 | 🔄 进行中 | 全量提取已启动（批次 #2，42,900 器件） |
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
