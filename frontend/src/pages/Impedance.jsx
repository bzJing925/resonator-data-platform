import React, { useCallback, useEffect, useMemo, useState } from 'react';
import I from '../components/Icons.jsx';
import { LineChart } from '../components/Charts.jsx';
import { listBatches, listBatchFiles, getFileCurve } from '../api/endpoints.js';
import { usePageState } from '../contexts/PageStateContext.jsx';

const MAX_PLOT = 30;
const PAGE_SIZE = 100;
const PALETTE = [
  '#2c79f6', '#c2410c', '#0e9488', '#7b3fe4', '#d97706',
  '#0891b2', '#be185d', '#4338ca', '#059669', '#9333ea',
  '#2563eb', '#ea580c', '#14b8a6', '#8b5cf6', '#f59e0b',
];

function groupByFolder(files) {
  const groups = new Map();
  groups.set('', { label: '全部', files: [] });
  for (const f of files) {
    const folder = f.relpath.includes('/') ? f.relpath.split('/')[0] : '(根目录)';
    if (!groups.has(folder)) groups.set(folder, { label: folder, files: [] });
    groups.get(folder).files.push(f);
    groups.get('').files.push(f);
  }
  return groups;
}

const IMPEDANCE_INITIAL_STATE = {
  batchNo: '',
  folder: '',
  search: '',
  page: 1,
  selected: new Set(),
  curves: [],
  showMean: true,
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
    selected,
    curves,
    showMean,
  } = state;

  const setBatchNo = useCallback((v) => setState((s) => ({ ...s, batchNo: typeof v === 'function' ? v(s.batchNo) : v })), [setState]);
  const setFolder = useCallback((v) => setState((s) => ({ ...s, folder: typeof v === 'function' ? v(s.folder) : v })), [setState]);
  const setSearch = useCallback((v) => setState((s) => ({ ...s, search: typeof v === 'function' ? v(s.search) : v })), [setState]);
  const setPage = useCallback((v) => setState((s) => ({ ...s, page: typeof v === 'function' ? v(s.page) : v })), [setState]);
  const setSelected = useCallback((v) => setState((s) => ({ ...s, selected: typeof v === 'function' ? v(s.selected) : v })), [setState]);
  const setCurves = useCallback((v) => setState((s) => ({ ...s, curves: typeof v === 'function' ? v(s.curves) : v })), [setState]);
  const setShowMean = useCallback((v) => setState((s) => ({ ...s, showMean: typeof v === 'function' ? v(s.showMean) : v })), [setState]);

  // Transient state: re-fetched from the API on mount / batch change.
  const [batches, setBatches] = useState([]);
  const [files, setFiles] = useState([]);
  const [loadingFiles, setLoadingFiles] = useState(false);
  const [loadingCurves, setLoadingCurves] = useState(false);
  const [error, setError] = useState(null);

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
      return;
    }
    setLoadingFiles(true);
    setError(null);
    listBatchFiles(batchNo)
      .then((data) => {
        setFiles(data || []);
        setFolder('');
        setPage(1);
        setSelected(new Set());
        setCurves([]);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoadingFiles(false));
  }, [batchNo]);

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

  const toggleFile = (relpath) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(relpath)) next.delete(relpath);
      else next.add(relpath);
      return next;
    });
  };

  const togglePage = () => {
    const pageRelpaths = pageFiles.map((f) => f.relpath);
    const allChecked = pageRelpaths.every((r) => selected.has(r));
    setSelected((prev) => {
      const next = new Set(prev);
      for (const r of pageRelpaths) {
        if (allChecked) next.delete(r);
        else next.add(r);
      }
      return next;
    });
  };

  const clearSelection = () => setSelected(new Set());

  const plotSelected = async () => {
    if (selected.size === 0) return;
    setLoadingCurves(true);
    setError(null);
    setCurves([]);
    const toPlot = Array.from(selected).slice(0, MAX_PLOT);
    try {
      const results = await Promise.all(
        toPlot.map(async (relpath) => {
          try {
            const data = await getFileCurve(batchNo, relpath, 'z_mag_db');
            return {
              relpath,
              name: data.relpath.split('/').pop(),
              freq: data.freq_ghz,
              values: data.values,
            };
          } catch (e) {
            return { relpath, name: relpath.split('/').pop(), error: e.message };
          }
        })
      );
      setCurves(results.filter((r) => !r.error && r.freq.length));
      const errCount = results.filter((r) => r.error).length;
      if (errCount) setError(`${errCount} 条曲线加载失败`);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoadingCurves(false);
    }
  };

  const series = useMemo(() => {
    const out = curves.map((c, i) => ({
      x: c.freq,
      y: c.values,
      name: c.name,
      color: PALETTE[i % PALETTE.length],
      width: 1.1,
      opacity: 0.75,
    }));
    if (showMean && curves.length > 1) {
      const minFreq = Math.max(...curves.map((c) => c.freq[0]));
      const maxFreq = Math.min(...curves.map((c) => c.freq[c.freq.length - 1]));
      const n = 400;
      const grid = Array.from({ length: n }, (_, i) => minFreq + (maxFreq - minFreq) * (i / (n - 1)));
      const meanY = grid.map((f) => {
        let sum = 0;
        let cnt = 0;
        curves.forEach((c) => {
          const idx = c.freq.findIndex((x) => x >= f);
          if (idx > 0 && idx < c.freq.length) {
            const x0 = c.freq[idx - 1], x1 = c.freq[idx];
            const y0 = c.values[idx - 1], y1 = c.values[idx];
            const t = (f - x0) / (x1 - x0);
            sum += y0 + t * (y1 - y0);
            cnt += 1;
          }
        });
        return cnt ? sum / cnt : null;
      });
      out.push({
        x: grid,
        y: meanY,
        name: '均值',
        color: '#111827',
        width: 2.4,
        opacity: 1,
      });
    }
    return out;
  }, [curves, showMean]);

  return (
    <>
      <div className="toolbar">
        <span className="crumb">
          谐振器 <span style={{ color: 'var(--fg-4)' }}>›</span> <b>阻抗曲线</b>
        </span>
        <div className="spacer" />
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--fg-2)' }}>
          <input type="checkbox" checked={showMean} onChange={(e) => setShowMean(e.target.checked)} />
          显示均值
        </label>
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
                    <option key={k} value={k}>{k} ({groups.get(k).files.length})</option>
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

          <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 0, flex: 1, minHeight: 0, borderTop: '1px solid var(--border)' }}>
            {/* 文件列表 */}
            <div style={{ display: 'flex', flexDirection: 'column', borderRight: '1px solid var(--border)', minHeight: 0 }}>
              <div style={{ padding: '8px 10px', borderBottom: '1px solid var(--border)', background: 'var(--bg-panel-2)', display: 'flex', alignItems: 'center', gap: 8 }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--fg-2)', cursor: 'pointer' }}>
                  <input type="checkbox" checked={pageFiles.length > 0 && pageFiles.every((f) => selected.has(f.relpath))} onChange={togglePage} />
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
                  const checked = selected.has(f.relpath);
                  return (
                    <label
                      key={f.relpath}
                      title={f.relpath}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 8,
                        padding: '5px 10px',
                        fontSize: 11.5,
                        cursor: 'pointer',
                        background: checked ? 'var(--primary-soft)' : 'transparent',
                        color: checked ? 'var(--primary)' : 'var(--fg-2)',
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleFile(f.relpath)}
                        style={{ flexShrink: 0 }}
                      />
                      <span className="mono" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {f.name}
                      </span>
                      {f.computed && <span className="badge done" style={{ fontSize: 9, marginLeft: 'auto', flexShrink: 0 }}>已计算</span>}
                    </label>
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
              {series.length > 0 && (
                <LineChart
                  series={series}
                  showLegend
                  xLabel="频率 (GHz)"
                  yLabel="|Z| (dB)"
                />
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
