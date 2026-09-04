# 数据分析工作区 Phase 0 验收基线

- 日期：2026-09-03
- 范围：真实 Plotly、Electron E2E、固定数据、性能和字段契约
- 状态：已建立

## 1. 可执行入口

```bash
cd frontend

# Vitest：纯 scene/effect/fixture 测试
npm test

# Electron + Chromium + 真实 Plotly；性能用例默认跳过
npm run test:e2e

# 1 万、5 万、10 万行性能基准
npm run test:e2e:perf
```

E2E 使用 `ALN_E2E_URL` 启动隔离的 Electron 验收页，不启动后端、不读用户数据。生产启动路径在没有该环境变量时保持不变。

## 2. 固定 fixture

`frontend/src/features/analysis/__fixtures__/analysisRows.ts` 固定覆盖：

- 24 条具有唯一 `__rowId` 的原始行；
- `scatter`、`box`、`violin`、`swarm`、`line` 五类图形；
- `qs`、`qp`、`k2eff_pct` 三个 Y 指标；
- `Series`、`Parallel` 两个结构类型；
- 700、900、1200 三个面积；
- 当前后端字段 `area_um2` 与规范字段 `areaReality` 的对应关系。

## 3. 已知问题基线

`frontend/e2e/analysis/known-gaps.spec.ts` 用 Playwright expected-fail 标记六类现状问题，其中图形选择类展开为五种图形逐一执行。断言确实执行；问题修复后若断言意外通过，测试会失败，提示实施阶段移除 expected-fail 标记并把它转成回归门禁。

| 问题 | 当前可复现现象 | 后续阶段 |
|---|---|---|
| Electron 命令 | `Delete` 依赖局部前端焦点，没有桌面命令桥 | Phase 6 |
| 五类图形原始点选择 | 统计层/折线聚合点不能保证一条 marker 对应一条底层行 | Phase 2/3 |
| Dataset Tab 生命周期 | `+` 只有复制语义，没有新建、重命名、关闭、恢复 | Phase 4 |
| Worksheet | 表头/内容各算列宽，没有 pointer drag 连续选择 | Phase 4 |
| JMP 布局 | 仍是通用 X/Y/Z grid，没有结构类型/面积层级元数据 | Phase 2 |
| Origin 样式 | 没有数据点样式 Inspector 和稳定 override 模型 | Phase 5 |

`frontend/e2e/analysis/plotly.spec.ts` 另外用真实 Plotly 验证五类图形均能渲染，且渲染后的 trace 保留可反查底层行的 customdata。它不把统计 trace 误当成合格的原始点选择层；真实框选和稳定事件链路仍属于 Phase 2/3 的 expected-fail 验收项。

## 4. 字段来源与规范化契约

### `areaReality`

- 当前生产权威来源：数据库 `devices.area_um2`，类型为 nullable integer，查询接口以 `area_um2` 返回。
- 数据入口来源：`backend/app/core/extract.py` 通过映射表的端口面积写入 `area_um2`；XLSX/分享包也统一映射为 `area_um2`。
- 分析模型规范字段：`areaReality: number | null`。
- 输入别名：已规范化数据的 `areaReality`、参考/JMP 数据的 `Area_reality`、当前 API 的 `area_um2`。
- 冲突规则：已存在的规范字段优先；生产 API 行以 `area_um2` 为源；别名不能覆盖非空的权威值。
- 缺失规则：`null`、`undefined`、空字符串或非有限数值规范化为 `null`；有限数值与可安全解析的数值字符串转为 `number`。
- 原字段名必须记录在 Dataset 的字段映射中，不能通过重命名丢失来源。

### `structureType`

- 当前数据库模型、查询 schema、提取流程和映射表都没有结构类型字段。
- 当前前端仅创建 `structureType` 派生列，并在缺失时写入空字符串；没有可信的 Series/Parallel 推导规则。
- 不允许根据 `pf`、`fs/fp`、端口或文件名猜测结构类型。
- 分析模型规范字段：`structureType: string`；输入别名为 `structureType`、`structure_type` 和 `StructureType`。
- 值规则：字符串去除首尾空白；`null`、`undefined` 或仅空白内容规范化为 `''`，由图表展示层显示为“缺失”。
- 要让生产数据真正支持 JMP 外层分组，需要业务方指定权威来源，并在后端查询 schema 中暴露它；在此之前 Phase 1 只能建立规范化和来源映射，不能生成业务值。

## 5. 性能基线

环境：Mac16,1、Apple M4、16 GiB、macOS 26.3、Electron 42.6.1，窗口绘图区 1280×800。数据为固定生成器生成；每条原始行进入三个 Y 面板，因此 Plotly 实际发射点数为行数的三倍。

2026-09-03 的一次 warm run：

| 原始行 | 发射点 | Scene 构建 | 首次渲染 | 缩放 relayout | 选择高亮 restyle | 刷新 react | 连续平移 FPS |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 10,000 | 30,000 | 4.3 ms | 86.3 ms | 2.5 ms | 29.6 ms | 7.2 ms | 120.1 |
| 50,000 | 150,000 | 7.7 ms | 112.2 ms | 3.2 ms | 44.1 ms | 24.1 ms | 67.5 |
| 100,000 | 300,000 | 10.6 ms | 185.2 ms | 4.2 ms | 90.0 ms | 39.9 ms | 37.0 |

解释与限制：

- 这是可重复的合成数据基线，不代表所有分组、箱形/小提琴统计和真实数据库查询成本。
- “选择”测量当前高亮路径的 `restyle(selectedpoints)`，不是人工拖动手势时间。
- 100,000 行、三个 Y 面板下连续平移明显低于 60 FPS，已经为 Phase 7 提供可量化瓶颈。
- 基准不设置通过阈值；Phase 0 只记录现状，后续优化阶段再根据目标硬件设门禁。
