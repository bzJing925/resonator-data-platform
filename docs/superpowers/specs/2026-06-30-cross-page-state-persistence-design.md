# 跨页面状态保留设计

## 背景

前端使用 React Router 6，页面组件在路由切换时会卸载，导致所有 `useState` 状态丢失。用户反馈需要：

1. 左侧导航栏切换页面时保留其他页面的操作状态。
2. 绘图页面（Explore、Impedance）切出去后再回来，已绘制的图要保留。
3. 上传/处理中的文件状态要保留（上传任务进度已有全局浮窗，但 Upload 表单状态会丢失）。

## 需求澄清结果

- **范围**：所有页面（重点覆盖 Explore、Impedance、Upload、Batches、BatchDetail、Mappings）。
- **持久化时长**：跨会话长期保留（浏览器关闭重开后仍恢复）。
- **图表数据**：保留配置 + 缓存绘图数据；大数据量时自动降级为仅保留配置。
- **实现方案**：Context + localStorage + 大数据自动降级。

## 总体方案

新增全局 `PageStateContext` 与 `usePageState(routeKey, initialState, options)` hook，把各页面状态从本地 `useState` 提升到全局上下文，并持久化到 `localStorage`。

### PageStateContext 职责

- 维护 `stateMap: Record<routeKey, any>`。
- 首次加载时从 `localStorage`（key: `aln_page_state_v1`）恢复。
- 状态变化后防抖写入 `localStorage`（500ms）。
- 透明地序列化/反序列化 `Set` 和 `Map`。
- 按 `dataKeys` 识别大数据字段，超过 `maxDataBytes` 时从持久化中剔除，保留在内存中。

### 页面接入方式

每个页面使用 `usePageState` 替换需要保留的 `useState`：

```jsx
const [state, setState] = usePageState('explore', initialState, {
  dataKeys: ['rows'],
  maxDataBytes: 1024 * 1024,
});
```

页面内部可继续解构状态，并通过封装好的 setter 更新单个字段：

```jsx
const setChartType = useCallback(
  (v) => setState((s) => ({ ...s, chartType: typeof v === 'function' ? v(s.chartType) : v })),
  [setState]
);
```

## 各页面保留状态

| 页面 | 保留字段 | 数据字段 |
|---|---|---|
| Explore | chartType, xFields, yFields, zField, waferZ, waferFacet, filters, limit, rows, stats, hiddenIds, prevHiddenIds | rows |
| Impedance | batchNo, folder, search, page, selected, curves, showMean | curves |
| Upload | mappingId, fStart, fEnd, deembed, deembedMethod | 无 |
| Batches | page, search | 无 |
| BatchDetail | page, waferFilter, pfFilter | 无 |
| Mappings | selected, name | 无 |

不保留的 transient 状态：loading、error、activeDevice、exporting、files（FileList 不可序列化）。

## 边界处理

- **localStorage 配额超限**：写入失败时静默丢弃，不影响页面运行。
- **数据字段过大**：超过 `maxDataBytes` 时自动剔除；恢复后若缺失数据，页面显示空状态，用户可重新查询。
- **旧状态不兼容**：用 `initialState` 合并回填缺失字段。
- **Set/Map**：通过 `__type` 标记透明序列化。
- **不可序列化对象**：File/Blob/Error/函数不会被持久化；若出现在 data 中，JSON 序列化会将其转为 `{}` 或 `null`。

## 文件变更

- 新增：`frontend/src/contexts/PageStateContext.jsx`
- 修改：`frontend/src/App.jsx`
- 修改：`frontend/src/pages/Explore.jsx`（并移除旧的 `aln_explore_state_v1` 逻辑）
- 修改：`frontend/src/pages/Impedance.jsx`
- 修改：`frontend/src/pages/Upload.jsx`
- 修改：`frontend/src/pages/Batches.jsx`
- 修改：`frontend/src/pages/BatchDetail.jsx`
- 修改：`frontend/src/pages/Mappings.jsx`

## 验证

1. 启动前端开发服务器：`cd frontend && npm run dev`
2. 在 Explore 配置坐标轴、筛选并运行查询生成图表。
3. 切换到其他页面后再切回 Explore，验证图表和筛选保留。
4. 在 Impedance 选择批次、勾选文件、绘制曲线，切换后验证保留。
5. 在 Upload 选择 mapping、频率范围、去嵌选项，切换后验证表单保留。
6. 刷新浏览器，验证状态从 localStorage 恢复。
7. 运行 `npm run build` 无报错。

## 依赖

无新增 npm 依赖，仅使用 React Context + localStorage。
