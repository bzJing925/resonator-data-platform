# 批次详情页器件列表合并 + 阻抗曲线页 Z11/Z22 选择 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在批次详情页合并同一原始文件名的器件行并新增端口列；在阻抗曲线页支持按 Z11/Z22 选择并绘制对应端口曲线。

**架构：** 后端 `/api/files/curve` 新增 `port` 查询参数，由 `compute_sparam_curve` 按 S11/S22 读取 `net.s` 对角分量；前端批次详情表格在内存中对当前页按 `original_filename` 排序并计算 `rowSpan`；阻抗曲线页将选择键改为 `relpath#port`，并为每个文件渲染 Z11/Z22 白底黑字复选框。

**技术栈：** FastAPI + SQLAlchemy 2.0、React 18 + TypeScript + Plotly.js、skrf

---

## 文件清单

| 文件 | 职责 |
|---|---|
| `backend/app/core/curves.py` | 曲线计算核心，新增 `port` 参数决定读取 S11/S22 |
| `backend/app/schemas/file.py` | `FileCurveResponse` 增加 `port` 字段 |
| `backend/app/api/files.py` | `/api/files/curve` 接收 `port` 并透传给曲线计算 |
| `backend/tests/core/test_curves.py` | 补充 S22 与 1-port 异常单元测试 |
| `frontend/src/types/index.ts` | `Device` 增加 `s_param_port` |
| `frontend/src/api/endpoints.ts` | `getFileCurve` 增加 `port` 参数 |
| `frontend/src/pages/BatchDetail.tsx` | 当前页排序、rowSpan 计算、静态列序调整 |
| `frontend/src/pages/Impedance.tsx` | 双选框、选择键、曲线名称后缀、指标面板去重 |
| `frontend/src/components/Charts.tsx` | `LineChart` 图例位置调整为上方外侧 |

---

## 任务 1：后端 `compute_sparam_curve` 支持 `port`

**文件：**
- 修改：`backend/app/core/curves.py`
- 测试：`backend/tests/core/test_curves.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/tests/core/test_curves.py` 末尾追加：

```python
def _make_network_2port(n: int = 5) -> skrf.Network:
    """构造一个 2-port 测试网络，S11 与 S22 取值不同。"""
    freq = np.linspace(1e9, 3e9, n)
    s = np.zeros((n, 2, 2), dtype=complex)
    z0 = np.full((n, 2), 50.0)
    for i in range(n):
        s[i, 0, 0] = complex(0.5 * np.cos(i * 0.5), 0.3 * np.sin(i * 0.5))
        s[i, 1, 1] = complex(0.2 * np.cos(i * 0.7), 0.4 * np.sin(i * 0.7))
    return skrf.Network(f=freq, s=s, z0=z0)


def test_compute_sparam_curve_port_s22() -> None:
    """S22 端口的曲线应与 S11 不同。"""
    net = _make_network_2port()
    s11 = compute_sparam_curve(net, "z_mag_db", "S11")
    s22 = compute_sparam_curve(net, "z_mag_db", "S22")
    assert s11["values"] != s22["values"]


def test_compute_sparam_curve_port_s22_on_1port_raises() -> None:
    """1-port 网络请求 S22 应抛 ValueError。"""
    net = _make_network()
    with pytest.raises(ValueError, match="S22"):
        compute_sparam_curve(net, "z_mag_db", "S22")
```

- [ ] **步骤 2：运行测试验证失败**

```bash
cd /Users/jingbozuo/Projects/aln-data-master/backend
uv run pytest tests/core/test_curves.py::test_compute_sparam_curve_port_s22 -v
uv run pytest tests/core/test_curves.py::test_compute_sparam_curve_port_s22_on_1port_raises -v
```

预期：两条测试均因 `compute_sparam_curve` 不接受第三个参数而失败。

- [ ] **步骤 3：编写最少实现代码**

修改 `backend/app/core/curves.py`：

```python
CurveParam = Literal["s11_db", "s11_phase", "s11_re_im", "z_mag_db", "z_phase"]
Port = Literal["S11", "S22"]
PARAM_CHOICES: tuple[str, ...] = (
    "s11_db",
    "s11_phase",
    "s11_re_im",
    "z_mag_db",
    "z_phase",
)


def compute_sparam_curve(
    net: skrf.Network, param: CurveParam, port: Port = "S11"
) -> dict[str, Any]:
    """根据 skrf.Network 计算指定曲线。"""
    if port not in ("S11", "S22"):
        raise ValueError(f"不支持的端口: {port}")

    freq_ghz = (net.f / 1e9).tolist()

    if port == "S22" and net.nports < 2:
        raise ValueError("S22 需要 2 端口网络")

    s = net.s[:, 0, 0] if port == "S11" else net.s[:, 1, 1]

    if param == "s11_db":
        values = (20 * np.log10(np.maximum(np.abs(s), 1e-12))).tolist()
        return {"freq_ghz": freq_ghz, "values": values}

    if param == "s11_phase":
        values = [float(v) for v in np.degrees(np.unwrap(np.angle(s)))]
        return {"freq_ghz": freq_ghz, "values": values}

    if param == "s11_re_im":
        return {
            "freq_ghz": freq_ghz,
            "values_re": np.real(s).tolist(),
            "values_im": np.imag(s).tolist(),
        }

    z0 = net.z0[0, 0] if port == "S11" else net.z0[1, 1]
    z = z0 * (1 + s) / (1 - s)

    if param == "z_mag_db":
        values = (20 * np.log10(np.maximum(np.abs(z), 1e-12))).tolist()
        return {"freq_ghz": freq_ghz, "values": values}

    if param == "z_phase":
        values = [float(v) for v in np.degrees(np.unwrap(np.angle(z)))]
        return {"freq_ghz": freq_ghz, "values": values}

    raise ValueError(f"不支持的曲线类型: {param}")
```

- [ ] **步骤 4：运行测试验证通过**

```bash
cd /Users/jingbozuo/Projects/aln-data-master/backend
uv run pytest tests/core/test_curves.py -v
```

预期：全部通过。

- [ ] **步骤 5：Commit**

```bash
cd /Users/jingbozuo/Projects/aln-data-master
git add backend/app/core/curves.py backend/tests/core/test_curves.py
git commit -m "feat(core): compute_sparam_curve 支持 S11/S22 端口选择

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 2：后端 `/api/files/curve` 接收并透传 `port`

**文件：**
- 修改：`backend/app/schemas/file.py`
- 修改：`backend/app/api/files.py`

- [ ] **步骤 1：修改响应模型**

修改 `backend/app/schemas/file.py` 中的 `FileCurveResponse`：

```python
class FileCurveResponse(BaseModel):
    """直接从文件读取的 S 参数 / 阻抗曲线。"""

    batch_no: str
    relpath: str
    param: str
    port: str = "S11"
    freq_ghz: list[float]
    values: list[float]
    values_re: list[float] | None = None
    values_im: list[float] | None = None
```

- [ ] **步骤 2：修改 API 端点**

修改 `backend/app/api/files.py`：

```python
from app.core.curves import PARAM_CHOICES, Port, compute_sparam_curve
```

然后修改 `get_file_curve`：

```python
@router.get("/curve", response_model=FileCurveResponse)
def get_file_curve(
    db: DbSession,
    batch_no: Annotated[str, Query(...)],
    relpath: Annotated[str, Query(...)],
    param: Annotated[str, Query()] = "z_mag_db",
    port: Annotated[Port, Query()] = "S11",
) -> FileCurveResponse:
    """直接从批次解压目录读取指定文件的 S 参数 / 阻抗曲线（无需先入库）。"""
    if param not in PARAM_CHOICES:
        raise HTTPException(status_code=400, detail=f"param 必须是 {','.join(PARAM_CHOICES)} 之一")

    batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    if batch is None:
        raise HTTPException(status_code=404, detail=f"批次 {batch_no} 不存在")

    base_dir = batch_files_dir(batch_no)
    target_path = _find_actual_path(base_dir, relpath)

    try:
        net = _read_network(target_path, batch.process_type or "S1P")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"读取 S 参数文件失败: {exc}") from exc

    try:
        curve = compute_sparam_curve(net, param, port)  # type: ignore[arg-type]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return FileCurveResponse(
        batch_no=batch_no,
        relpath=relpath,
        param=param,
        port=port,
        freq_ghz=curve["freq_ghz"],
        values=curve.get("values", []),
        values_re=curve.get("values_re"),
        values_im=curve.get("values_im"),
    )
```

- [ ] **步骤 3：运行曲线相关测试**

```bash
cd /Users/jingbozuo/Projects/aln-data-master/backend
uv run pytest tests/core/test_curves.py -v
```

预期：通过。

- [ ] **步骤 4：Commit**

```bash
cd /Users/jingbozuo/Projects/aln-data-master
git add backend/app/schemas/file.py backend/app/api/files.py
git commit -m "feat(api): /files/curve 支持 port 查询参数

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 3：前端类型与 API 封装

**文件：**
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/api/endpoints.ts`

- [ ] **步骤 1：补充 Device 类型**

在 `frontend/src/types/index.ts` 的 `Device` 接口中：

```typescript
export interface Device {
  id?: number | string;
  batch_no?: string;
  original_filename?: string;
  display_name?: string;
  mark?: string;
  wafer?: string | number;
  folder_name?: string;
  coord?: string;
  x?: number;
  y?: number;
  eg?: number;
  fl?: number;
  ag?: number;
  pf?: string;
  area_n?: number;
  area_um2?: number;
  fs_ghz?: number;
  fp_ghz?: number;
  zs_ohm?: number;
  zp_ohm?: number;
  qs?: number;
  qp?: number;
  qs_bodeq?: number;
  qp_bodeq?: number;
  dbqs?: number;
  dbqp?: number;
  bodeq_fitted?: unknown;
  bodeq_smooth?: unknown;
  bodeq_raw?: unknown;
  fbode_ghz?: number;
  k2eff_pct?: number;
  fp2_ghz?: number;
  fs2_ghz?: number;
  zp2_ohm?: number;
  zs2_ohm?: number;
  deembedded?: boolean;
  s_param_path?: string;
  s_param_port?: string;
  [key: string]: unknown;
}
```

- [ ] **步骤 2：修改 getFileCurve**

修改 `frontend/src/api/endpoints.ts`：

```typescript
export const getFileCurve = (batchNo: string, relpath: string, param = 'z_mag_db', port = 'S11') =>
  api
    .get('/files/curve', {
      params: { batch_no: batchNo, relpath, param, port },
    })
    .then((r: AxiosResponse<{ relpath: string; port: string; freq_ghz: number[]; values: number[] }>) => r.data);
```

- [ ] **步骤 3：运行前端类型检查**

```bash
cd /Users/jingbozuo/Projects/aln-data-master/frontend
npm run build
```

预期：`npm run build` 成功完成（仅类型检查阶段通过即可，生产构建本身也会通过）。

- [ ] **步骤 4：Commit**

```bash
cd /Users/jingbozuo/Projects/aln-data-master
git add frontend/src/types/index.ts frontend/src/api/endpoints.ts
git commit -m "feat(frontend): Device 增加 s_param_port，getFileCurve 支持 port 参数

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 4：批次详情页表格合并与列序调整

**文件：**
- 修改：`frontend/src/pages/BatchDetail.tsx`

- [ ] **步骤 1：调整列定义并添加 rowSpan 辅助函数**

替换 `COLUMN_DEFS`（移除 `original_filename`，并将 `mark` fallback 改为 `代号`）：

```typescript
const COLUMN_DEFS: ColumnDef[] = [
  // 标识
  { key: 'mark', fallback: '代号', type: 'text' },
  { key: 'wafer', fallback: '晶圆 Wafer', type: 'text', render: (d) => (d.wafer != null ? `W${d.wafer}` : '—') },
  { key: 'coord', fallback: '坐标 Coord', type: 'text' },
  { key: 'x', fallback: 'X', type: 'num', digits: 0 },
  { key: 'y', fallback: 'Y', type: 'num', digits: 0 },
  { key: 'pf', fallback: '合格 P/F', type: 'text', render: (d) => <span className={d.pf === 'Y' ? 'pass' : 'fail'}>{d.pf || '—'}</span> },
  // 工艺
  { key: 'eg', fallback: '电极间隙 EG', type: 'num', digits: 2 },
  { key: 'fl', fallback: '指长 FL', type: 'num', digits: 2 },
  { key: 'ag', fallback: '孔径 AG', type: 'num', digits: 2 },
  { key: 'area_um2', fallback: '面积 (μm²)', type: 'num', digits: 0 },
  // 主参数
  { key: 'fs_ghz', fallback: '串联谐振 fs (GHz)', type: 'num', digits: 4 },
  { key: 'fp_ghz', fallback: '并联谐振 fp (GHz)', type: 'num', digits: 4 },
  { key: 'zs_ohm', fallback: '串联阻抗 Zs (Ω)', type: 'num', digits: 2 },
  { key: 'zp_ohm', fallback: '并联阻抗 Zp (Ω)', type: 'num', digits: 2 },
  { key: 'qs', fallback: '串联 Qs', type: 'num', digits: 0 },
  { key: 'qp', fallback: '并联 Qp', type: 'num', digits: 0 },
  // BodeQ
  { key: 'fbode_ghz', fallback: 'BodeQ 频率 (GHz)', type: 'num', digits: 4 },
  { key: 'qs_bodeq', fallback: 'BodeQ 串联 Qs', type: 'num', digits: 0 },
  { key: 'qp_bodeq', fallback: 'BodeQ 并联 Qp', type: 'num', digits: 0 },
  { key: 'k2eff_pct', fallback: '有效机电耦合 k²eff (%)', type: 'num', digits: 2 },
  // 中间峰
  { key: 'fp2_ghz', fallback: '二次并联 fp2 (GHz)', type: 'num', digits: 4 },
  { key: 'fs2_ghz', fallback: '二次串联 fs2 (GHz)', type: 'num', digits: 4 },
];
```

在 `COLUMN_DEFS` 之后添加：

```typescript
interface OriginalCellInfo {
  rowSpan: number;
  showCell: boolean;
}

function computeOriginalSpans(items: Device[]): Map<string | number, OriginalCellInfo> {
  const counts = new Map<string, number>();
  for (const d of items) {
    const key = d.original_filename || '';
    counts.set(key, (counts.get(key) || 0) + 1);
  }

  const info = new Map<string | number, OriginalCellInfo>();
  const seen = new Set<string>();
  for (const d of items) {
    const key = d.original_filename || '';
    if (!seen.has(key)) {
      info.set(d.id as string | number, { rowSpan: counts.get(key) || 1, showCell: true });
      seen.add(key);
    } else {
      info.set(d.id as string | number, { rowSpan: 0, showCell: false });
    }
  }
  return info;
}
```

- [ ] **步骤 2：修改 DeviceRow 组件**

更新 `DeviceRowProps` 与 `DeviceRow`：

```typescript
interface DeviceRowProps {
  device: Device;
  columns: ColumnDef[];
  fmtCell: (d: Device, c: ColumnDef) => React.ReactNode;
  onRowClick: (d: Device) => void;
  onDownload: (d: Device) => void;
  originalSpan?: OriginalCellInfo;
}

const DeviceRow = memo(function DeviceRow({ device, columns, fmtCell, onRowClick, onDownload, originalSpan }: DeviceRowProps) {
  const d = device;
  return (
    <tr style={{ cursor: 'pointer' }} onClick={() => onRowClick(d)}>
      {originalSpan?.showCell && (
        <td rowSpan={originalSpan.rowSpan} className="mono" style={{ verticalAlign: 'middle' }}>
          {d.original_filename || '—'}
        </td>
      )}
      <td className="mono">{d.id || '—'}</td>
      <td>{d.s_param_port || '—'}</td>
      {columns.map((c) => (
        <td key={c.key} className={c.type === 'num' ? 'num mono' : ''}>{fmtCell(d, c)}</td>
      ))}
      <td>
        <button className="btn ghost sm" onClick={(e) => { e.stopPropagation(); onRowClick(d); }} title="查看曲线">
          <I.curve size={12} />
        </button>
        <button
          className="btn ghost sm"
          onClick={(e) => { e.stopPropagation(); onDownload(d); }}
          title="下载原始 S 参数文件"
        >
          <I.download size={12} />
        </button>
      </td>
    </tr>
  );
});
```

- [ ] **步骤 3：表格头、空状态与行渲染**

在组件内，将 `const items = devices.items || [];` 替换为排序后的数据与 span 信息：

```typescript
const items = devices.items || [];
const sortedItems = useMemo(() => {
  return [...items].sort((a, b) =>
    String(a.original_filename || '').localeCompare(String(b.original_filename || ''))
  );
}, [items]);
const originalSpans = useMemo(() => computeOriginalSpans(sortedItems), [sortedItems]);
```

修改表格头：

```tsx
<thead>
  <tr>
    <th>原始文件名</th>
    <th>器件 ID</th>
    <th>端口</th>
    {columns.map((c) => (
      <th key={c.key} className={c.type === 'num' ? 'num' : ''}>{c.header}</th>
    ))}
    <th></th>
  </tr>
</thead>
```

修改空状态 `colSpan`：

```tsx
<td colSpan={columns.length + 4} className="dim" style={{ textAlign: 'center', padding: 24 }}>
  暂无器件
</td>
```

修改行渲染：

```tsx
{sortedItems.map((d) => (
  <DeviceRow
    key={d.id || `${d.wafer}-${d.coord}`}
    device={d}
    columns={columns}
    fmtCell={fmtCell}
    onRowClick={handleRowClick}
    onDownload={handleDownloadS1p}
    originalSpan={originalSpans.get(d.id as string | number)}
  />
))}
```

- [ ] **步骤 4：构建验证**

```bash
cd /Users/jingbozuo/Projects/aln-data-master/frontend
npm run build
```

预期：构建成功。

- [ ] **步骤 5：Commit**

```bash
cd /Users/jingbozuo/Projects/aln-data-master
git add frontend/src/pages/BatchDetail.tsx
git commit -m "feat(frontend): 批次详情页合并原始文件名并新增端口列

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 5：阻抗曲线页 Z11/Z22 双选框

**文件：**
- 修改：`frontend/src/pages/Impedance.tsx`

- [ ] **步骤 1：添加端口类型与辅助函数**

在 `frontend/src/pages/Impedance.tsx` 中，添加类型与 `filePorts` 辅助函数：

```typescript
type Port = 'S11' | 'S22';

function filePorts(f: FileEntry, processType?: string): Port[] {
  const name = f.name.toLowerCase();
  if (name.endsWith('.s2p')) return ['S11', 'S22'];
  if (name.endsWith('.snp') && (processType === 'S2P' || processType === 'BOTH')) return ['S11', 'S22'];
  return ['S11'];
}
```

- [ ] **步骤 2：添加 PortCheckbox 组件**

在文件顶部或组件外部添加：

```typescript
function PortCheckbox({ port, checked, onChange }: { port: Port; checked: boolean; onChange: () => void }) {
  const label = port === 'S11' ? 'Z11' : 'Z22';
  return (
    <label
      title={label}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: 32,
        height: 18,
        border: '1px solid #000',
        borderRadius: 3,
        background: '#fff',
        color: '#000',
        fontSize: 9,
        cursor: 'pointer',
        userSelect: 'none',
        boxShadow: checked ? 'inset 0 0 0 1px #000' : 'none',
      }}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={onChange}
        style={{ position: 'absolute', opacity: 0, width: 0, height: 0 }}
      />
      {label}
    </label>
  );
}
```

- [ ] **步骤 3：替换选择逻辑与统计逻辑**

在组件内获取当前批次对象：

```typescript
const batch = useMemo(() => batches.find((b) => b.batch_no === batchNo), [batches, batchNo]);
```

将 `toggleFile` 替换为：

```typescript
const togglePort = useCallback((relpath: string, port: Port) => {
  setSelected((prev) => {
    const next = new Set(prev);
    const key = `${relpath}#${port}`;
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  });
}, []);
```

将 `togglePage` 替换为：

```typescript
const togglePage = useCallback(() => {
  const pageKeys = pageFiles.flatMap((f) =>
    filePorts(f, batch?.process_type).map((p) => `${f.relpath}#${p}`)
  );
  const allChecked = pageKeys.length > 0 && pageKeys.every((k) => selected.has(k));
  setSelected((prev) => {
    const next = new Set(prev);
    for (const k of pageKeys) {
      if (allChecked) next.delete(k);
      else next.add(k);
    }
    return next;
  });
}, [pageFiles, selected, batch?.process_type]);
```

修改 `selectedMetrics` 为按文件去重：

```typescript
const selectedMetrics = useMemo(() => {
  const seen = new Set<string>();
  const out = [];
  selected.forEach((key) => {
    const relpath = key.split('#')[0];
    if (seen.has(relpath)) return;
    seen.add(relpath);
    const m = metricsMap.get(relpath);
    if (m) out.push(m);
  });
  return out;
}, [selected, metricsMap]);
```

修改 `selectedUncomputedCount` 为按文件去重：

```typescript
const selectedUncomputedCount = useMemo(() => {
  const seen = new Set<string>();
  let count = 0;
  selected.forEach((key) => {
    const relpath = key.split('#')[0];
    if (seen.has(relpath)) return;
    seen.add(relpath);
    const f = files.find((file) => file.relpath === relpath);
    if (f && !f.computed) count += 1;
  });
  return count;
}, [selected, files]);
```

- [ ] **步骤 4：修改绘制函数以使用 port**

替换 `plotSelected` 中的曲线加载部分：

```typescript
const toPlot = Array.from(selected as Set<string>).slice(0, MAX_PLOT);
const results = await Promise.all(
  toPlot.map(async (key) => {
    const [relpath, port] = key.split('#') as [string, Port];
    try {
      const data = await getFileCurve(batchNo, relpath, 'z_mag_db', port);
      const { x, y } = decimate(data.freq_ghz, data.values, MAX_POINTS_PER_CURVE);
      const filename = data.relpath.split('/').pop() || relpath;
      const label = port === 'S11' ? 'Z11' : 'Z22';
      return {
        key,
        name: `${filename} (${label})`,
        freq: x,
        values: y,
        error: null as null,
      };
    } catch (e: any) {
      return { key, name: `${relpath} (${port === 'S11' ? 'Z11' : 'Z22'})`, error: e.message || String(e) };
    }
  })
);
setCurves(results.filter((r): r is { key: string; name: string; freq: number[]; values: number[] } => !r.error && !!r.freq.length));
```

- [ ] **步骤 5：替换文件列表渲染**

替换 `pageFiles.map((f) => { ... })` 内部为：

```tsx
{pageFiles.map((f) => {
  const ports = filePorts(f, batch?.process_type);
  const s11Checked = selected.has(`${f.relpath}#S11`);
  const s22Checked = selected.has(`${f.relpath}#S22`);
  const anyChecked = (ports.includes('S11') && s11Checked) || (ports.includes('S22') && s22Checked);
  return (
    <div
      key={f.relpath}
      title={f.relpath}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '5px 10px',
        fontSize: 11.5,
        cursor: 'pointer',
        background: 'transparent',
        color: anyChecked ? 'var(--fg-1)' : 'var(--fg-2)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0 }}>
        {ports.includes('S11') && (
          <PortCheckbox port="S11" checked={s11Checked} onChange={() => togglePort(f.relpath, 'S11')} />
        )}
        {ports.includes('S22') && (
          <PortCheckbox port="S22" checked={s22Checked} onChange={() => togglePort(f.relpath, 'S22')} />
        )}
      </div>
      <span className="mono" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {f.name}
      </span>
      {f.computed ? (
        <span className="badge done" style={{ fontSize: 9, marginLeft: 'auto', flexShrink: 0 }}>已计算</span>
      ) : (
        <span className="badge" style={{ fontSize: 9, marginLeft: 'auto', flexShrink: 0, background: 'var(--bg-3)', color: 'var(--fg-3)' }}>未计算</span>
      )}
    </div>
  );
})}
```

- [ ] **步骤 6：构建验证**

```bash
cd /Users/jingbozuo/Projects/aln-data-master/frontend
npm run build
```

预期：构建成功。

- [ ] **步骤 7：Commit**

```bash
cd /Users/jingbozuo/Projects/aln-data-master
git add frontend/src/pages/Impedance.tsx
git commit -m "feat(frontend): 阻抗曲线页支持 Z11/Z22 双选框与曲线图例后缀

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 6：LineChart 图例置于上方外侧

**文件：**
- 修改：`frontend/src/components/Charts.tsx`

- [ ] **步骤 1：修改 LineChart 的 legend 与 margin**

在 `frontend/src/components/Charts.tsx` 的 `LineChart` 组件中，修改 `layout`：

```typescript
const layout = useMemo(() => {
  const markerShapes = markers.map((m) => ({
    type: 'line', x0: m.x, x1: m.x, yref: 'paper', y0: 0, y1: 1,
    line: { color: m.color || '#c97a16', width: 1, dash: 'dash' },
  }));
  const annotations = markers.map((m) => ({
    x: m.x, yref: 'paper', y: 1, text: m.label || '', showarrow: false,
    font: { color: m.color || '#c97a16', size: 10 }, bgcolor: '#fff',
  }));
  return {
    ...baseLayout,
    showlegend: !!showLegend,
    legend: { orientation: 'h', y: 1.12, x: 0, xanchor: 'left', yanchor: 'bottom' },
    margin: { ...baseLayout.margin, t: 44 },
    xaxis: { ...baseLayout.xaxis, title: xLabel || 'x', type: xIsCategory ? 'category' : undefined },
    yaxis: { ...baseLayout.yaxis, title: yLabel || 'y' },
    shapes: [...markerShapes, ...extraShapes],
    annotations,
  };
}, [xLabel, yLabel, showLegend, xIsCategory, markers, extraShapes]);
```

- [ ] **步骤 2：构建验证**

```bash
cd /Users/jingbozuo/Projects/aln-data-master/frontend
npm run build
```

预期：构建成功。

- [ ] **步骤 3：Commit**

```bash
cd /Users/jingbozuo/Projects/aln-data-master
git add frontend/src/components/Charts.tsx
git commit -m "feat(frontend): LineChart 图例移至绘图区上方外侧

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 7：回归验证

**注意：** 当前工作区中存在与此需求无关的未提交改动（自动重命名重复 `batch_no`）。执行本任务前，请先用 `git status` 确认只提交/暂存了本计划涉及的文件，避免混入其他改动。

- [ ] **步骤 1：后端单元测试**

```bash
cd /Users/jingbozuo/Projects/aln-data-master/backend
uv run pytest tests/core/test_curves.py -v
uv run pytest tests/api -v -m "not integration"
```

预期：
- `tests/core/test_curves.py` 全部通过。
- API 测试（排除需要运行 worker/server 的 integration）全部通过。

- [ ] **步骤 2：前端构建**

```bash
cd /Users/jingbozuo/Projects/aln-data-master/frontend
npm run build
```

预期：构建成功，无 TypeScript 错误。

- [ ] **步骤 3：手动验证清单**

启动完整开发环境（`./bootstrap.sh up` 或分别启动 `uvicorn` + `celery` + `npm run dev`）后，在一个包含 `.s2p` 文件的批次上验证：

1. **批次详情页**
   - 同一 `.s2p` 文件的两行器件，「原始文件名」单元格合并为一个。
   - 列序为：原始文件名 → 器件 ID → 端口 → 代号 → …
   - 「端口」列分别显示 `S11`、`S22`。
   - 翻页、筛选 wafer/P/F 后合并仅作用于当前页，不跨页。

2. **阻抗曲线页**
   - `.s2p` 文件左侧显示两个复选框：Z11、Z22；`.s1p` 仅显示 Z11。
   - 复选框为白底、黑字、黑边框。
   - 选中 Z22 后点击「绘制选中」，曲线图例显示 `filename.s2p (Z22)`。
   - 同一文件同时选中 Z11、Z22 时，图例中出现两条曲线，颜色按绘制顺序分配。
   - 图例位于图表上方，不遮挡曲线。
   - 右侧指标面板在双选时只统计一次该文件（去重）。

- [ ] **步骤 4：最终 Commit / 推送**

如果以上验证全部通过，且 `git status` 中仅包含本计划相关文件，则完成合并或推送：

```bash
cd /Users/jingbozuo/Projects/aln-data-master
git log --oneline -8
# 确认提交记录干净后，按需 push
# git push origin <branch>
```

---

## 自检

- **规格覆盖度：** 批次详情合并、端口列、列序、阻抗曲线双选框、Z11/Z22 曲线、图例位置、白底黑字复选框均已对应任务。
- **占位符扫描：** 无 TODO/待定/模糊描述；每步均包含实际代码与命令。
- **类型一致性：** `port` 在后端为 `Literal["S11", "S22"]`，前端为 `Port` 类型；`getFileCurve` 签名前后一致；`Device.s_param_port` 与后端 `DEVICE_COLUMNS` 一致。
