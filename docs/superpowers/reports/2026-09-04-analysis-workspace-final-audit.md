# 数据分析工作区 Phase 0–7 最终审计

- 日期：2026-09-04
- Spec：`docs/superpowers/plans/2026-09-03-analysis-workspace-jmp-origin-refactor.md`
- 审计对象：当前未提交的分析工作区变更
- 结论：自动化发布门禁通过；无高严重度发现

本次变更尚未形成 commit，因此没有擅自选择 branch/commit 作为 review fixed point。审计直接对照计划与当前工作区，并保留用户已有的 backend、Upload 等无关修改。

## Standards

未发现阻塞项。

- 遵循仓库约定，前端使用 TypeScript/TSX、两空格缩进、现有组件和 CSS，没有引入 UI 框架或重量级状态库。
- Dataset、Selection、Command、Scene 的核心转换保持为可单测的纯函数；Electron 只通过受限 preload API 暴露分析命令、上下文菜单和剪贴板写入。
- 使用 `npm` 完成 typecheck、Vitest、Playwright Electron 和生产构建；没有全局安装依赖。
- `git diff --check` 通过；没有提交 `.env`、数据库、release 包或构建产物。
- 审计中发现并修复了输入控件快捷键冲突：输入框、select、textarea、contenteditable 内不再触发分析删除/复制/撤销/全选，Electron 应用菜单会退回原生编辑命令。

非阻塞构建提示：Plotly 独立 chunk 约 4.87 MB，Vite 继续报告大 chunk warning。该依赖在本次重构前已存在，且已经单独分包，不属于功能或发布阻塞。

## Spec

Phase 0–7 的主链路已经实现并进入回归门禁：

- 真实 Plotly/Electron fixture、性能基线和字段来源契约；
- Dataset Registry、稳定 rowId、共享 Selection、Command 和 Undo/Redo；
- JMP 式结构/面积层级、多 Y 纵排、原始点/统计/汇总分层；
- 五类图形选择、双向联动、底层删除和 Y 轴 autorange；
- Worksheet 虚拟滚动、严格列对齐、拖选/键盘选择和 Tab 生命周期；
- Origin 式稀疏点样式及 Undo/Redo；
- Electron 菜单、右键菜单、快捷键和受限 IPC；
- Scene cache、ScatterGL、100k 视觉分桶、全屏状态保持和性能验收。

审计记录两项有意保留的实现边界：

1. 计划写有“删除只重算受影响的指标和分组”。当前 revision 只使目标 Dataset 的 Scene 失效，其他 Dataset 的 Scene 会复用缓存；但目标图内部仍整体派生。验收数据每行都包含三个 Y 指标，删除会实际影响三个指标。100k 删除 1% 并完成 Scene + Plotly 更新约 97.6ms，尚未证明值得把 trace 拆成 metric/group 子图并承担选择、样式和 Plotly diff 复杂度。
2. Worksheet 使用固定行高窗口化、overscan 和 memoized grid，任何时刻只渲染可视行，没有全表刷新；未额外引入外部 row-level subscription store。现有基准没有显示可视行重渲染是瓶颈，因此遵循 Phase 7“只优化已确认瓶颈”的原则保持简单实现。

另有一个已在 Phase 0 记录的外部数据依赖：生产数据尚无可信 `structureType` 来源。当前规范化模型不会猜测业务值，而是保留缺失桶；需要业务方指定权威来源后才能在后端补齐。

## 最终门禁

```text
TypeScript typecheck                    PASS
Vitest                                  118 passed, 1 skipped
Electron + real Plotly E2E              19 passed
10k / 50k / 100k performance benchmark 3 passed
Vite + Electron production build        PASS
git diff --check                         PASS
```

性能明细见 `2026-09-04-analysis-phase7-release-acceptance.md`。视觉观感、目标业务数据和真实设备上的最终人工走查仍应由产品验收者执行；自动化结果不能替代业务方对 `structureType` 数据来源的确认。
