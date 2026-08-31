# Explore 页 X 轴标签自动使用 Mapping 结构参数

## 背景

当前数据分析页（`Explore`）的 X 轴可选标签由前端硬编码列表 `X_AXIS_FIELD_NAMES` 控制：

```ts
['eg', 'fl', 'ag', 'batch_no', 'wafer', 'pf', 'x', 'y', 'fs_ghz']
```

其中只有 `eg`、`fl`、`ag`、`pf` 来自 mapping（对照表）解析后的结构参数，
`area_um2`、`area_n` 等同为 mapping 衍生的结构参数却未出现在列表中。
同时 `batch_no`、`wafer`、`fs_ghz`、`x`、`y` 等字段并非器件结构参数。

用户希望 X 轴标签能够"自动检查对照表上的所有器件结构参数"，并将这些参数作为可选标签。

## 需求目标

- Explore 页的 X 轴下拉框只展示由 mapping 解析到 `devices` 表的器件结构参数。
- 不再需要手动维护 X 轴字段白名单；结构参数集合由业务定义决定。
- 保持现有选择器 UI、多选行为、状态持久化逻辑不变。

## 关键决策

| 问题 | 决策 |
|------|------|
| 哪些字段属于"器件结构参数"？ | mapping 解析后写入 `devices` 表的字段：`eg`、`fl`、`ag`、`pf`、`area_um2`、`area_n` |
| 作用页面 | 仅 `Explore`（数据分析）页 |
| 替换方式 | 完全替换现有 `X_AXIS_FIELD_NAMES` |
| "mapping 包含"如何判定 | 以 schema 中是否存在该字段为准（所有 mapping 均包含全部列） |
| 实现方案 | 前端硬编码结构参数列表（方案 1） |

## 设计方案

### 1. 改动范围

仅修改前端文件：

- `frontend/src/pages/Explore.tsx`

后端与 API 无需改动。

### 2. 字段列表

将现有 `X_AXIS_FIELD_NAMES` 替换为：

```ts
const X_AXIS_FIELD_NAMES = [
  'eg',        // 工艺/类别
  'fl',        // 工艺/类别
  'ag',        // 工艺/类别
  'pf',        // Pass/Fail
  'area_um2',  // 面积（μm²）
  'area_n',    // 区域编号
];
```

若 `AXIS_FIELD_LABELS` 中尚未包含 `area_um2`、`area_n`，补充对应中文标签。

### 3. 选择器行为

- `axisFieldOptions(X_AXIS_FIELD_NAMES, fields)` 继续把字段名解析为带 `label`、`section`、`isCategorical` 的选项对象。
- `AxisFieldCheckList` 与 `Inspector` 中的 X 轴选择器继续复用这些选项，UI 表现不变。
- `usePageState` 对选中字段的持久化逻辑不变。

### 4. 边界情况

- **字段无数据**：结构参数仍显示在下拉框中，图表区域无对应数据点，与现有行为一致。
- **旧 localStorage 状态包含已移除字段**：组件将缺失字段视为无效选中，不报错。
- **未选择任何 batch**：X 轴选项仍然展示全部结构参数（schema 存在即包含）。

### 5. 测试策略

当前前端尚未引入测试框架，本次以手动验证为主：

1. 打开 Explore 页。
2. 点击 X 轴字段选择器，确认列表仅包含 `eg`、`fl`、`ag`、`pf`、`area_um2`、`area_n`。
3. 依次选择各字段，确认图表能正常渲染。
4. 选择多个字段，确认多 X 轴视图工作正常。

后续若项目引入 vitest/jest，可补充单元测试覆盖 `axisFieldOptions` 过滤逻辑与 `X_AXIS_FIELD_NAMES` 内容。

## 待实现计划

调用 `writing-plans` 技能生成详细实现计划，包括文件修改、验证步骤与提交信息。
