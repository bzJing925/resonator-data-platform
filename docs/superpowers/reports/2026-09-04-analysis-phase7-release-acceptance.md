# 数据分析工作区 Phase 7 发布验收

- 日期：2026-09-04
- 范围：Scene 缓存、大数据视觉层、交互性能、全屏状态、发布前自动化验收
- 状态：通过

## 1. 实现结论

- JMP Scene 以 Dataset ID、revision、图形类型、Y 字段、固定分组维度和画布尺寸为键，使用最多 8 项的进程内 LRU 缓存；相同数据表从 Worksheet 返回图表时不再重建 Scene。
- `structureType` / `areaReality` 分组键每个 revision 只计算一次，所有 Y 指标复用行到 slot 的映射。
- 每个 Y 指标超过 50,000 个原始点时，只对 raw-point 视觉层做确定性分桶。统计层仍读取完整 Dataset；每个代表点通过 `__memberRowIds` 保留全部底层 rowId，选择、删除和样式高亮不会失去来源映射。
- 选择和样式仍使用 Plotly `restyle`；缩放和平移留在 Plotly 内部；全屏尺寸变化由 `ResizeObserver` 合并到一帧后调用 `Plotly.Plots.resize`。
- Worksheet 保持固定行高虚拟滚动、overscan 和 memoized grid；删除命令只提升目标 Dataset 的 revision，其他 Dataset 的 Scene 保持缓存。当前验收数据的每行均包含三个 Y 值，因此删除行会实际影响当前图的三个指标，不伪造更细粒度的“未受影响指标”。
- 未引入 Web Worker。100k Scene 构建低于 80ms，主要瓶颈原本是 300k WebGL 点的渲染与选择；视觉层优化后交互已超过 60 FPS，线程通信没有基准依据。

## 2. 性能结果

环境沿用 Phase 0：Mac16,1、Apple M4、16 GiB、macOS 26.3、Electron 42.6.1，绘图区 1280×800。每行包含三个 Y 指标。“映射点”是所有底层 rowId 的总数，“显示点”是送给 Plotly 的 marker 数。

| 原始行 | 映射点 | 显示点 | Scene | 缓存命中 | 首次渲染 | 缩放 | 选择 | 刷新 | 批量删除并重建 | 平移 FPS |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10,000 | 30,000 | 30,000 | 30.2 ms | 0.0 ms | 255.2 ms | 5.2 ms | 12.2 ms | 5.3 ms | 19.0 ms | 118.6 |
| 50,000 | 150,000 | 150,000 | 51.0 ms | 0.1 ms | 128.3 ms | 12.7 ms | 32.0 ms | 8.9 ms | 65.1 ms | 110.9 |
| 100,000 | 300,000 | 150,000 | 75.0 ms | 0.0 ms | 154.2 ms | 10.3 ms | 23.8 ms | 16.2 ms | 97.6 ms | 112.7 |

100k 改动前同轮对照为：300,000 显示点、Scene 81.0ms、首次渲染 204.8ms、选择 81.4ms、平移 55.8 FPS。最终验收运行中，选择耗时下降约 71%，平移超过 100 FPS，同时仍映射全部 300,000 个底层行点。

## 3. 全屏与状态验收

真实 Electron + Plotly E2E 覆盖：

- 全屏时 `.app.analysis-fullscreen` 隐藏左侧主导航，Workspace 底部 Tab 保持可见；
- 筛选和配置入口在工具栏保持独立可收起；
- 进入、退出全屏后，筛选状态、当前图形类型、当前 Tab、选择和样式均保持；
- 全屏宽度变化触发 Plotly resize，不替换 Dataset；
- Plotly 内部滚轮缩放前后，React 页面 render count 不变；
- 缓存 Scene 被 Plotly 就地修改 marker 后，Undo 仍使用不可变 Scene 默认样式正确恢复。

## 4. 验证命令

```bash
cd frontend
npm run typecheck
npm test -- --run
npx playwright test e2e/analysis/known-gaps.spec.ts --reporter=line
npx playwright test e2e/analysis/plotly.spec.ts --reporter=line
ALN_RUN_PERF=1 npx playwright test e2e/analysis/performance.spec.ts --reporter=line
npm run build
```

性能数值是单次 warm run，会受系统调度影响；自动门禁只检查完整 rowId 映射、显示点不膨胀和 Scene 缓存命中小于 5ms，不把瞬时 FPS 设为硬失败条件。
