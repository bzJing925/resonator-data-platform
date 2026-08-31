# 批次详情页器件列表合并 + 阻抗曲线页 Z11/Z22 选择设计

## 背景

- 一个 `.s2p` 文件上传后会被拆分为 `S11`、`S22` 两个器件，分别对应 `Z11`、`Z22` 两条阻抗曲线。
- 当前批次详情页器件列表中，同一文件的两个器件各自显示完整的“原始文件名”，视觉上重复；且没有明确显示端口列。
- 当前阻抗曲线页面左侧文件列表只有一个复选框，选中 `.s2p` 文件时固定绘制 `Z11`，无法选择 `Z22`。

## 需求

1. **批次详情页器件列表**
   - 将同一 `original_filename` 的连续行合并为一个单元格。
   - 新列序：原始文件名 → 器件 ID → 端口 → 代号 → 其他列。
   - 端口列显示 `s_param_port`（`S11` / `S22`）。

2. **阻抗曲线页面**
   - 文件名前由单个复选框改为两个复选框：Z11、Z22。
   - `.s1p` 文件只显示 Z11 复选框；`.s2p` 文件显示 Z11、Z22 两个复选框。
   - 复选框样式改为白底黑字。
   - 图例中加入 Z11/Z22 后缀，例如 `filename.s2p (Z11)`。
   - 图例置于绘图区上方外侧，避免遮挡曲线。

## 方案

### 1. 后端 `/api/files/curve` 支持端口选择

在 `GET /api/files/curve` 新增可选查询参数 `port`，取值 `S11`（默认）或 `S22`。

- 对于 `.s1p` 文件：仅允许 `S11`；请求 `S22` 时返回 `400`。
- 对于 `.s2p` 文件：
  - `S11` 读取 `net.s[:, 0, 0]`，阻抗计算使用 `net.z0[0, 0]`。
  - `S22` 读取 `net.s[:, 1, 1]`，阻抗计算使用 `net.z0[1, 1]`。
- 对于 `.snp` 文件：按当前 `process_type` 识别为 S1P/S2P 后按上述规则处理。

`compute_sparam_curve` 增加 `port` 参数，内部决定读取哪个 S 参数分量。

### 2. 前端数据类型

`frontend/src/types/index.ts` 的 `Device` 接口增加 `s_param_port?: string`。

> 后端 `list_batch_devices` 已返回该字段（`DEVICE_COLUMNS` 包含模型所有列），前端只需补齐类型。

### 3. 批次详情页表格

**排序与合并逻辑**

- 从 API 拿到当前页 `items` 后，按 `original_filename` 升序排列，使同一文件的两行连续。
- 遍历排序后的数组，计算每个 `original_filename` 组的 `rowSpan`（即连续出现的行数）。
- 仅对当前页内连续行合并；不跨页合并。

**列结构**

| 列 | 来源 |
|---|---|
| 原始文件名 | `device.original_filename`，带 `rowSpan` |
| 器件 ID | `device.id` |
| 端口 | `device.s_param_port` |
| 代号 | `device.mark` |
| 后续列 | 现有 `COLUMN_DEFS` 从 `mark` 开始 |

实现时从 `COLUMN_DEFS` 中移除 `original_filename`，改在表格第一列静态渲染；端口列也在表格头静态添加。

### 4. 阻抗曲线页面

**选择键**

- 将选择标识从 `relpath` 改为 `${relpath}#${port}`，其中 `port ∈ {S11, S22}`。
- `.s1p` 文件仅提供 `S11` 选项；`.s2p` 文件提供 `S11`、`S22` 两个选项。

**渲染**

- 每行文件左侧显示 Z11 / Z22 两个按钮式复选框：
  - 未选中：白底、黑字、黑边框。
  - 选中：保持白底黑字，通过边框颜色或 subtle 阴影表示激活。
- 取消当前选中行的蓝色/主题色高亮背景（保持透明）。

**绘制**

- 对每个选中项调用 `getFileCurve(batchNo, relpath, 'z_mag_db', port)`。
- 曲线名称：`${filename} (Z11)` / `${filename} (Z22)`。
- 曲线颜色按当前调色板顺序分配。

**图例**

- `LineChart` 的 Plotly `legend` 配置改为：
  - `orientation: 'h'`
  - `y: 1.12`（位于绘图区上方外侧）
  - `x: 0`
  - `xanchor: 'left'`
  - `yanchor: 'bottom'`
- 同时适当增加 `layout.margin.t`，为上方图例留出空间。

## 涉及文件

| 文件 | 改动 |
|---|---|
| `frontend/src/types/index.ts` | `Device` 增加 `s_param_port` |
| `frontend/src/pages/BatchDetail.tsx` | 排序、rowSpan 计算、静态列序调整 |
| `frontend/src/pages/Impedance.tsx` | 双选框、选择键、样式、图例名称 |
| `frontend/src/api/endpoints.ts` | `getFileCurve` 增加 `port` 参数 |
| `frontend/src/components/Charts.tsx` | `LineChart` 图例位置调整为上方外侧 |
| `backend/app/core/curves.py` | `compute_sparam_curve` 支持 `port` |
| `backend/app/api/files.py` | `/api/files/curve` 接收 `port` 参数 |
| `backend/app/schemas/file.py` | `FileCurveResponse` 增加 `port` |

## 测试策略

- 单元测试 `compute_sparam_curve`：对同一 `.s2p` 分别传 `S11`、`S22`，验证 `values` 不同。
- 前端手动验证：
  - 批次详情页：同一 `.s2p` 对应的两个器件行正确合并原始文件名，列序正确。
  - 阻抗曲线页：`.s2p` 显示两个复选框，`.s1p` 只显示一个；选中 Z22 后绘制的是 S22 曲线；图例带 `(Z22)` 后缀且位于上方。

## 待办/风险

- 合并单元格后表格行的 hover/click 行为需保持正常。
- 若同一页内同一文件名出现超过两次（理论上应为两次），rowSpan 机制可自然处理。
- 图例在上方时，文件名称较长可能导致折行；必要时后续迭代改为右侧或滚动。
