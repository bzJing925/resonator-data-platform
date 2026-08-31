import React, { useCallback, useEffect, useMemo, useState } from 'react';
import I from '../components/Icons';
import { LineChart } from '../components/Charts';
import {
  listBatches,
  listBatchFiles,
  listBatchDevices,
  getFileCurve,
} from '../api/endpoints';
import { usePageState } from '../contexts/PageStateContext';
import type { Batch, FileEntry } from '../types';

const MAX_PLOT = 30;
const PAGE_SIZE = 100;
const MAX_POINTS_PER_CURVE = 2000;
const PALETTE = [
  '#2c79f6', '#c2410c', '#0e9488', '#7b3fe4', '#d97706',
  '#0891b2', '#be185d', '#4338ca', '#059669', '#9333ea',
  '#2563eb', '#ea580c', '#14b8a6', '#8b5cf6', '#f59e0b',
];

const METRIC_FIELDS = [
  { key: 'fs_ghz', label: 'fs', unit: 'GHz', digits: 4 },
  { key: 'fp_ghz', label: 'fp', unit: 'GHz', digits: 4 },
  { key: 'qs', label: 'Qs', digits: 0 },
  { key: 'qp', label: 'Qp', digits: 0 },
  { key: 'k2eff_pct', label: 'k²eff', unit: '%', digits: 2 },
  { key: 'zs_ohm', label: 'Zs', unit: 'Ω', digits: 2 },
  { key: 'zp_ohm', label: 'Zp', unit: 'Ω', digits: 2 },
];

function groupByFolder(files: FileEntry[]) {
  const groups = new Map<string, { label: string; files: FileEntry[] }>();
  groups.set('', { label: '全部', files: [] });
  for (const f of files) {
    const folder = f.relpath.includes('/') ? f.relpath.split('/')[0] : '(根目录)';
    if (!groups.has(folder)) groups.set(folder, { label: folder, files: [] });
    groups.get(folder).files.push(f);
    groups.get('').files.push(f);
  }
  return groups;
}

function decimate(x, y, maxPoints) {
  if (!x || !y || x.length <= maxPoints) return { x, y };
  const step = x.length / maxPoints;
  const nx = [];
  const ny = [];
  for (let i = 0; i < maxPoints; i++) {
    const idx = Math.min(Math.round(i * step), x.length - 1);
    nx.push(x[idx]);
    ny.push(y[idx]);
  }
  return { x: nx, y: ny };
}

function formatNum(v, digits) {
  if (v == null || Number.isNaN(v)) return '—';
  return Number(v).toFixed(digits);
}

type Port = 'S11' | 'S22';

function filePorts(f: FileEntry, processType?: string): Port[] {
  const lowerName = f.name.toLowerCase();
  if (lowerName.endsWith('.s2p')) return ['S11', 'S22'];
  if (lowerName.endsWith('.snp')) {
    const pt = (processType || '').toUpperCase();
    if (pt === 'S2P' || pt === 'BOTH') return ['S11', 'S22'];
  }
  return ['S11'];
}

// 后端 devices 表以 s_param_path + s_param_port 作为唯一键：
// - 未去嵌的 .s2p 直接用原始 s2p 相对路径
// - 去嵌后的 .s2p 用生成的 *_de.s1p 路径
// - 独立的 .s1p 用自身路径
function resolveMetricRelpath(relpath: string, port: Port, deembedded: boolean): string {
  const lower = relpath.toLowerCase();
  if (lower.endsWith('.s1p') || lower.endsWith('.s1p.gz')) return relpath;
  if (!lower.endsWith('.s2p') && !lower.endsWith('.snp')) return relpath;
  // 未去嵌时后端存的就是原始 .s2p 路径
  if (!deembedded) return relpath;
  // Windows 后端返回的路径分隔符是反斜杠，必须保持一致才能命中 metricsMap
  const sep = relpath.includes('\\') ? '\\' : '/';
  const stem = relpath.slice(0, -4);
  const portSuffix = port === 'S11' ? '_S11' : '_S22';
  return `${port === 'S11' ? 'S11_de' : 'S22_de'}${sep}${stem}${portSuffix}_de.s1p`;
}

function metricKey(relpath: string, port: Port, deembedded: boolean): string {
  return `${resolveMetricRelpath(relpath, port, deembedded)}#${port}`;
}

function PortCheckbox({ port, checked, onChange }: { port: Port; checked: boolean; onChange: () => void }) {
  const label = port === 'S11' ? 'Z11' : 'Z22';
  return (
    <label
      style={{
        position: 'relative',
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        minWidth: 40,
        padding: '2px 6px',
        fontSize: 10,
        fontWeight: checked ? 700 : 500,
        borderRadius: 4,
        cursor: 'pointer',
        userSelect: 'none',
        background: '#ffffff',
        color: '#111827',
        border: '1px solid #111827',
        boxShadow: checked ? 'inset 0 0 0 1px #111827' : 'none',
      }}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={onChange}
        style={{ position: 'absolute', opacity: 0, width: 0, height: 0, pointerEvents: 'none' }}
      />
      {label}
    </label>
  );
}

const IMPEDANCE_INITIAL_STATE = {
  batchNo: '',
  folder: '',
  search: '',
  page: 1,
  selected: new Set<string>(),
  curves: [],
};

export default function Impedance() {
  const [state, setState] = usePageState(
    'impedance',
    IMPEDANCE_INITIAL_STATE,
    { dataKeys: ['curves'], maxDataBytes: 1024 * 1024 },
  );
  const {
    batchNo,
    folder,
    search,
    page,
    selected: rawSelected,
    curves: rawCurves,
  } = state;

  // 兼容旧 persisted state：selected 可能被存成 null / 非 Set，这里做防御性归一化
  const selected = rawSelected instanceof Set ? rawSelected : new Set<string>();
  const curves = Array.isArray(rawCurves) ? rawCurves : [];

  const setBatchNo = useCallback((v: string | ((prev: string) => string)) => setState((s) => ({ ...s, batchNo: typeof v === 'function' ? v(s.batchNo) : v })), [setState]);
  const setFolder = useCallback((v: string | ((prev: string) => string)) => setState((s) => ({ ...s, folder: typeof v === 'function' ? v(s.folder) : v })), [setState]);
  const setSearch = useCallback((v: string | ((prev: string) => string)) => setState((s) => ({ ...s, search: typeof v === 'function' ? v(s.search) : v })), [setState]);
  const setPage = useCallback((v: number | ((prev: number) => number)) => setState((s) => ({ ...s, page: typeof v === 'function' ? v(s.page) : v })), [setState]);
  const setSelected = useCallback((v: Set<string> | ((prev: Set<string>) => Set<string>)) => setState((s) => {
    const prev = s.selected instanceof Set ? s.selected : new Set<string>();
    return { ...s, selected: typeof v === 'function' ? v(prev) : v };
  }), [setState]);
  const setCurves = useCallback((v: any[] | ((prev: any[]) => any[])) => setState((s) => {
    const prev = Array.isArray(s.curves) ? s.curves : [];
    return { ...s, curves: typeof v === 'function' ? v(prev) : v };
  }), [setState]);

  // Transient state: re-fetched from the API on mount / batch change.
  const [batches, setBatches] = useState<Batch[]>([]);
  const [files, setFiles] = useState<FileEntry[]>([]);
  const [loadingFiles, setLoadingFiles] = useState<boolean>(false);
  const [loadingCurves, setLoadingCurves] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [metricsMap, setMetricsMap] = useState<Map<string, any>>(new Map());
  const [loadingMetrics, setLoadingMetrics] = useState<boolean>(false);

  // 当前在图表上点击高亮的曲线 key（不属于持久化状态）
  const [highlightedKey, setHighlightedKey] = useState<string | null>(null);

  // 修复旧 persisted state 里 selected / curves 为 null 的情况，避免刷新后再次崩溃
  useEffect(() => {
    if (!(rawSelected instanceof Set)) setSelected(new Set());
    if (!Array.isArray(rawCurves)) setCurves([]);
  }, [rawSelected, rawCurves, setSelected, setCurves]);

  const batch = useMemo(() => batches.find((b) => b.batch_no === batchNo), [batches, batchNo]);

  useEffect(() => {
    listBatches({ size: 200 })
      .then((res) => setBatches(res.items || []))
      .catch(() => setBatches([]));
  }, []);

  useEffect(() => {
    if (!batchNo) {
      setFiles([]);
      setFolder('');
      setSearch('');
      setPage(1);
      setSelected(new Set());
      setCurves([]);
      setHighlightedKey(null);
      setMetricsMap(new Map());
      return;
    }
    setLoadingFiles(true);
    setError(null);
    listBatchFiles(batchNo, true)
      .then((data) => {
        setFiles(data || []);
        setFolder('');
        setPage(1);
        setSelected(new Set());
        setCurves([]);
        setHighlightedKey(null);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoadingFiles(false));

    setLoadingMetrics(true);
    listBatchDevices(batchNo, { size: 1000 })
      .then((res) => {
        const map = new Map();
        (res.items || []).forEach((d) => {
          if (d.s_param_path) {
            const port: Port = d.s_param_port === 'S22' ? 'S22' : 'S11';
            map.set(`${d.s_param_path}#${port}`, d);
          }
        });
        setMetricsMap(map);
      })
      .catch(() => setMetricsMap(new Map()))
      .finally(() => setLoadingMetrics(false));
  }, [batchNo]);

  // 指标加载完成后，把曲线图例从文件名更新为 description
  useEffect(() => {
    if (!batch || curves.length === 0 || metricsMap.size === 0) return;
    setCurves((prev) => {
      let changed = false;
      const next = prev.map((c) => {
        const [relpath, port] = c.key.split('#') as [string, Port];
        const key = metricKey(relpath, port, !!batch.deembedded);
        const device = metricsMap.get(key);
        if (!device?.description) return c;
        const suffix = port === 'S11' ? 'Z11' : 'Z22';
        const newName = `${device.description} (${suffix})`;
        if (newName === c.name) return c;
        changed = true;
        return { ...c, name: newName };
      });
      return changed ? next : prev;
    });
  }, [metricsMap, batch, curves.length]);

  const groups = useMemo(() => groupByFolder(files), [files]);

  const filtered = useMemo(() => {
    let list = folder ? groups.get(folder)?.files || [] : files;
    if (search.trim()) {
      const kw = search.trim().toLowerCase();
      list = list.filter((f) => f.name.toLowerCase().includes(kw));
    }
    return list;
  }, [files, folder, groups, search]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const pageFiles = useMemo(() => {
    const start = (page - 1) * PAGE_SIZE;
    return filtered.slice(start, start + PAGE_SIZE);
  }, [filtered, page]);

  const togglePort = (relpath: string, port: Port) => {
    const key = `${relpath}#${port}`;
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
    // 取消选中某条曲线时，如果它正是高亮项，也一并取消高亮
    if (!selected.has(key)) {
      // 即将新增选中，不影响高亮
    } else {
      setHighlightedKey((k) => (k === key ? null : k));
    }
  };

  const togglePage = () => {
    const pagePortKeys = pageFiles.flatMap((f) =>
      filePorts(f, batch?.process_type).map((port) => `${f.relpath}#${port}`)
    );
    const allChecked = pagePortKeys.length > 0 && pagePortKeys.every((key) => selected.has(key));
    setSelected((prev) => {
      const next = new Set(prev);
      for (const key of pagePortKeys) {
        if (allChecked) next.delete(key);
        else next.add(key);
      }
      return next;
    });
    // 全选/取消全选时清除高亮，避免高亮项不再被绘制
    setHighlightedKey(null);
  };

  const clearSelection = () => {
    setSelected(new Set());
    setHighlightedKey(null);
  };

  const plotSelected = async () => {
    if (selected.size === 0) return;
    setLoadingCurves(true);
    setError(null);
    setCurves([]);
    setHighlightedKey(null);
    const toPlot = Array.from(selected as Set<string>).slice(0, MAX_PLOT);
    try {
      const results: ({ key: string; relpath: string; name: string; freq: number[]; values: number[]; error?: null } | { key: string; relpath: string; name: string; error: string; freq?: undefined; values?: undefined })[] = await Promise.all(
        toPlot.map(async (key) => {
          const [relpath, port] = key.split('#') as [string, Port];
          const mkey = metricKey(relpath, port, !!batch?.deembedded);
          try {
            const data = await getFileCurve(batchNo, relpath, 'z_mag_db', port);
            const { x, y } = decimate(data.freq_ghz, data.values, MAX_POINTS_PER_CURVE);
            const filename = data.relpath.split('/').pop() || relpath;
            const suffix = port === 'S11' ? 'Z11' : 'Z22';
            const device = metricsMap.get(mkey);
            const label = device?.description || filename;
            return {
              key,
              relpath,
              name: `${label} (${suffix})`,
              freq: x,
              values: y,
              error: null as null,
            };
          } catch (e: any) {
            const filename = relpath.split('/').pop() || relpath;
            const suffix = port === 'S11' ? 'Z11' : 'Z22';
            const device = metricsMap.get(mkey);
            const label = device?.description || filename;
            return { key, relpath, name: `${label} (${suffix})`, error: e.message || String(e) };
          }
        })
      );
      setCurves(results.filter((r): r is { key: string; relpath: string; name: string; freq: number[]; values: number[] } => !r.error && !!r.freq.length));
      const errCount = results.filter((r) => r.error).length;
      if (errCount) setError(`${errCount} 条曲线加载失败`);
    } catch (e: any) {
      setError(e.message || String(e));
    } finally {
      setLoadingCurves(false);
    }
  };

  const handleSeriesClick = useCallback((key: string) => {
    setHighlightedKey((prev) => (prev === key ? null : key));
  }, []);

  const series = useMemo(() => {
    const hasHighlight = highlightedKey != null && curves.some((c) => c.key === highlightedKey);
    return curves.map((c, i) => {
      const isHighlighted = c.key === highlightedKey;
      const isDimmed = hasHighlight && !isHighlighted;
      return {
        x: c.freq,
        y: c.values,
        name: c.name,
        color: PALETTE[i % PALETTE.length],
        width: isHighlighted ? 2.4 : 1.1,
        opacity: isDimmed ? 0 : (isHighlighted ? 1 : 0.85),
        visible: isDimmed ? ('legendonly' as const) : true,
        // 把曲线 key 带到每个点上，供 Plotly onClick 反查
        customdata: Array(c.freq.length).fill(c.key),
      };
    });
  }, [curves, highlightedKey]);

  const selectedRelpaths = useMemo(() => {
    const set = new Set<string>();
    selected.forEach((key) => {
      const [relpath] = key.split('#');
      set.add(relpath);
    });
    return set;
  }, [selected]);

  const selectedMetrics = useMemo(() => {
    const out = [];
    selected.forEach((key) => {
      const [relpath, port] = key.split('#') as [string, Port];
      const mkey = metricKey(relpath, port, !!batch?.deembedded);
      const m = metricsMap.get(mkey);
      if (m) out.push(m);
    });
    return out;
  }, [selected, metricsMap, batch?.deembedded]);

  const selectedUncomputedCount = useMemo(() => {
    let count = 0;
    selectedRelpaths.forEach((relpath) => {
      const f = files.find((file) => file.relpath === relpath);
      if (f && !f.computed) count += 1;
    });
    return count;
  }, [selectedRelpaths, files]);

  // 高亮曲线对应的单个 device 指标
  const highlightedMetric = useMemo(() => {
    if (!highlightedKey) return null;
    const [relpath, port] = highlightedKey.split('#') as [string, Port];
    const mkey = metricKey(relpath, port, !!batch?.deembedded);
    return metricsMap.get(mkey) || null;
  }, [highlightedKey, metricsMap, batch?.deembedded]);

  const highlightedCurveName = useMemo(() => {
    if (!highlightedKey) return '';
    const c = curves.find((x) => x.key === highlightedKey);
    return c?.name || '';
  }, [highlightedKey, curves]);

  const highlightedMetricValues = useMemo(() => {
    if (!highlightedMetric) return null;
    return METRIC_FIELDS.map((f) => ({ ...f, value: highlightedMetric[f.key] }));
  }, [highlightedMetric]);

  // 未高亮单条曲线时，在右侧面板显示已选曲线的指标最大/最小值
  const selectedMetricStats = useMemo(() => {
    if (selectedMetrics.length === 0) return null;
    return METRIC_FIELDS.map((f) => {
      const vals = selectedMetrics
        .map((m) => m[f.key])
        .filter((v) => typeof v === 'number' && Number.isFinite(v));
      if (vals.length === 0) return { ...f, min: null, max: null, count: 0 };
      return {
        ...f,
        min: Math.min(...vals),
        max: Math.max(...vals),
        count: vals.length,
      };
    });
  }, [selectedMetrics]);

  // 重新绘制或清空选择时，取消图表上的单条高亮
  useEffect(() => {
    setHighlightedKey(null);
  }, [curves, selected]);

  const pageAllChecked = pageFiles.length > 0 && pageFiles.every((f) =>
    filePorts(f, batch?.process_type).every((port) => selected.has(`${f.relpath}#${port}`))
  );

  return (
    <>
      <div className="toolbar">
        <span className="crumb">
          谐振器 <span style={{ color: 'var(--fg-4)' }}>›</span> <b>阻抗曲线</b>
        </span>
        <div className="spacer" />
      </div>

      <div style={{ flex: 1, overflow: 'auto', padding: 18 }}>
        <div className="chart-card" style={{ maxWidth: 1480, margin: '0 auto', height: 'calc(100% - 0px)', display: 'flex', flexDirection: 'column' }}>
          <div className="chart-head">
            <span className="title">阻抗曲线 |Z| (dB)</span>
            <div className="row-flex" style={{ gap: 10, flexWrap: 'wrap' }}>
              <select
                className="input mono"
                style={{ minWidth: 180 }}
                value={batchNo}
                onChange={(e) => setBatchNo(e.target.value)}
              >
                <option value="">选择批次…</option>
                {batches.map((b) => (
                  <option key={b.batch_no} value={b.batch_no}>
                    {b.batch_no} ({b.device_count} 器件)
                  </option>
                ))}
              </select>
              <select
                className="input mono"
                style={{ minWidth: 120 }}
                value={folder}
                onChange={(e) => { setFolder(e.target.value); setPage(1); }}
                disabled={!batchNo}
              >
                <option value="">全部目录</option>
                {Array.from(groups.keys())
                  .filter((k) => k !== '')
                  .map((k) => (
                    <option key={k} value={k}>{k} ({groups.get(k)?.files.length || 0})</option>
                  ))}
              </select>
              <input
                className="input mono"
                placeholder="搜索文件名…"
                value={search}
                onChange={(e) => { setSearch(e.target.value); setPage(1); }}
                style={{ minWidth: 160 }}
              />
              <button className="btn" onClick={plotSelected} disabled={selected.size === 0 || loadingCurves}>
                <I.curve size={12} /> 绘制选中 ({selected.size})
              </button>
              <button className="btn" onClick={clearSelection} disabled={selected.size === 0}>
                清空
              </button>
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr 220px', gap: 0, flex: 1, minHeight: 0, borderTop: '1px solid var(--border)' }}>
            {/* 文件列表 */}
            <div style={{ display: 'flex', flexDirection: 'column', borderRight: '1px solid var(--border)', minHeight: 0 }}>
              <div style={{ padding: '8px 10px', borderBottom: '1px solid var(--border)', background: 'var(--bg-panel-2)', display: 'flex', alignItems: 'center', gap: 8 }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--fg-2)', cursor: 'pointer' }}>
                  <input type="checkbox" checked={pageAllChecked} onChange={togglePage} />
                  全选本页
                </label>
                <span className="mono dim" style={{ fontSize: 11, marginLeft: 'auto' }}>
                  {filtered.length} 个文件
                </span>
              </div>
              <div style={{ flex: 1, overflow: 'auto', padding: '6px 0' }}>
                {loadingFiles && <div className="dim" style={{ padding: 20, textAlign: 'center' }}>加载文件列表…</div>}
                {!loadingFiles && filtered.length === 0 && (
                  <div className="dim" style={{ padding: 20, textAlign: 'center' }}>无文件</div>
                )}
                {pageFiles.map((f) => {
                  const ports = filePorts(f, batch?.process_type);
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
                        background: 'transparent',
                        color: 'var(--fg-2)',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0 }}>
                        {ports.map((port) => (
                          <PortCheckbox
                            key={port}
                            port={port}
                            checked={selected.has(`${f.relpath}#${port}`)}
                            onChange={() => togglePort(f.relpath, port)}
                          />
                        ))}
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
              </div>
              {totalPages > 1 && (
                <div style={{ padding: '6px 10px', borderTop: '1px solid var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <button className="btn sm" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1}>上一页</button>
                  <span className="mono dim" style={{ fontSize: 11 }}>{page} / {totalPages}</span>
                  <button className="btn sm" onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page === totalPages}>下一页</button>
                </div>
              )}
            </div>

            {/* 图表 */}
            <div className="chart-body" style={{ minHeight: 480, border: 'none' }}>
              {loadingCurves && <div className="dim" style={{ padding: 60, textAlign: 'center' }}>绘制中…</div>}
              {error && (
                <div style={{ padding: 14, color: 'var(--fail)' }}>
                  <I.alert size={12} /> {error}
                </div>
              )}
              {!loadingCurves && curves.length === 0 && !error && (
                <div className="dim" style={{ padding: 60, textAlign: 'center' }}>
                  选择左侧文件后点击「绘制选中」查看阻抗曲线
                </div>
              )}
              {selected.size > MAX_PLOT && (
                <div style={{ padding: '8px 12px', fontSize: 11, color: '#92611a', background: 'rgba(255,195,0,0.08)', borderBottom: '1px solid rgba(255,195,0,0.3)' }}>
                  ⚠ 一次最多绘制 {MAX_PLOT} 条曲线，已自动取前 {MAX_PLOT} 个选中文件
                </div>
              )}
              {selectedUncomputedCount > 0 && (
                <div style={{ padding: '8px 12px', fontSize: 11, color: '#5b4a00', background: 'rgba(255,195,0,0.08)', borderBottom: '1px solid rgba(255,195,0,0.3)' }}>
                  ⚠ 选中的 {selectedUncomputedCount} 个文件尚未计算指标，曲线仍可绘制，但右侧指标面板不会显示其参数
                </div>
              )}
              {series.length > 0 && (
                <LineChart
                  series={series}
                  showLegend
                  xLabel="频率 (GHz)"
                  yLabel="|Z| (dB)"
                  onSeriesClick={handleSeriesClick}
                  onLegendSelect={handleSeriesClick}
                />
              )}
            </div>

            {/* 指标面板 */}
            <div style={{ borderLeft: '1px solid var(--border)', display: 'flex', flexDirection: 'column', minHeight: 0, background: 'var(--bg-panel-2)' }}>
              <div style={{ padding: '10px 12px', borderBottom: '1px solid var(--border)', fontWeight: 600, fontSize: 12, color: 'var(--fg-1)' }}>
                指标
              </div>
              <div style={{ flex: 1, overflow: 'auto', padding: '8px 10px' }}>
                {loadingMetrics && <div className="dim" style={{ fontSize: 11, padding: 8 }}>加载指标…</div>}
                {!loadingMetrics && !highlightedKey && selectedMetricStats && (
                  <>
                    <div className="dim" style={{ fontSize: 10, marginBottom: 10 }}>
                      已选 {selectedMetrics.length} 条曲线的指标范围（点击图例查看单条）
                    </div>
                    {selectedMetricStats.map((s) => (
                      <div key={s.key} style={{ marginBottom: 10, padding: '8px 10px', background: 'var(--bg-panel)', borderRadius: 6, border: '1px solid var(--border)' }}>
                        <div style={{ fontSize: 11, color: 'var(--fg-3)', marginBottom: 4 }}>
                          {s.label}{s.unit ? ` (${s.unit})` : ''}
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--fg-2)' }}>
                          <span>最小 <b className="mono" style={{ color: 'var(--primary)' }}>{formatNum(s.min, s.digits)}</b></span>
                          <span>最大 <b className="mono" style={{ color: 'var(--primary)' }}>{formatNum(s.max, s.digits)}</b></span>
                        </div>
                      </div>
                    ))}
                  </>
                )}
                {!loadingMetrics && !highlightedKey && !selectedMetricStats && (
                  <div className="dim" style={{ fontSize: 11, padding: 8 }}>
                    选择已计算指标的文件后可查看统计
                  </div>
                )}
                {!loadingMetrics && highlightedKey && !highlightedMetric && (
                  <div className="dim" style={{ fontSize: 11, padding: 8 }}>
                    该曲线对应的文件尚未计算指标
                  </div>
                )}
                {!loadingMetrics && highlightedMetric && highlightedMetricValues && (
                  <>
                    <div style={{ marginBottom: 12, padding: '8px 10px', background: 'var(--bg-panel)', borderRadius: 6, border: '1px solid var(--border)' }}>
                      <div className="dim" style={{ fontSize: 9, marginBottom: 2 }}>当前曲线</div>
                      <div className="mono" style={{ fontSize: 12, color: 'var(--fg-1)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {highlightedCurveName}
                      </div>
                    </div>
                    {highlightedMetricValues.map((s) => (
                      <div key={s.key} style={{ marginBottom: 10, padding: '8px 10px', background: 'var(--bg-panel)', borderRadius: 6, border: '1px solid var(--border)' }}>
                        <div style={{ fontSize: 11, color: 'var(--fg-3)', marginBottom: 4 }}>
                          {s.label}{s.unit ? ` (${s.unit})` : ''}
                        </div>
                        <div className="mono" style={{ fontSize: 13, color: 'var(--primary)' }}>{formatNum(s.value, s.digits)}</div>
                      </div>
                    ))}
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
