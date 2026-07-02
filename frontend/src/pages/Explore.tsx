import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import I from '../components/Icons.jsx';
import { UnifiedChartGrid, WaferMap } from '../components/Charts.jsx';
import useFields, { displayLabel } from '../hooks/useFields.js';
import { queryDevices, queryAggregate, exportCsv } from '../api/endpoints.js';
import DeviceModal from '../components/DeviceModal.jsx';
import FilterPanel from '../components/FilterPanel.jsx';

// Aggregation options for numeric Y/Z fields.
//   'all' is a UI sentinel meaning "no aggregation, plot raw rows".
//   The other ops map to backend AggOp values in /api/query/aggregate.
//   'mean' is rendered as 'avg' on the wire (backend uses SQL AVG).
const AGG_OPTIONS = [
  { key: 'all',    label: '不聚合' },
  { key: 'max',    label: '最大值' },
  { key: 'min',    label: '最小值' },
  { key: 'mean',   label: '平均值' },
  { key: 'p50',    label: '中位数 (p50)' },
  { key: 'p25',    label: '下四分位 (p25)' },
  { key: 'p75',    label: '上四分位 (p75)' },
];
// Wire name sent to backend metrics[].agg.
function aggWireName(uiKey) {
  return uiKey === 'mean' ? 'avg' : uiKey;
}

// UI label for an aggregation key.
function aggLabel(uiKey) {
  return AGG_OPTIONS.find((o) => o.key === uiKey)?.label || uiKey;
}

// Trigger a browser download for a returned Blob (used by CSV export).
function downloadBlob(blob, filename) {
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}

// Full export field list (mirrors the Device ORM model + virtual batch_no).
const EXPORT_FIELDS = [
  'id', 'batch_no',
  'original_filename', 'display_name', 'mark', 'wafer', 'folder_name', 'coord', 'x', 'y',
  'eg', 'fl', 'ag', 'pf', 'area_n', 'area_um2',
  'fs_ghz', 'fp_ghz', 'zs_ohm', 'zp_ohm', 'qs', 'qp',
  'qs_bodeq', 'qp_bodeq', 'dbqs', 'dbqp',
  'bodeq_fitted', 'bodeq_smooth', 'bodeq_raw', 'fbode_ghz', 'k2eff_pct',
  'fp2_ghz', 'fs2_ghz', 'zp2_ohm', 'zs2_ohm',
  'deembedded', 's_param_path',
];

// Sections returned by /api/query/fields. Used for grouping in pickers.
const SECTION_LABELS = {
  categorical: '类别字段',
  process: '工艺字段',
  geometric: '几何字段',
  numeric: '数值字段',
};
const SECTION_ORDER = ['categorical', 'process', 'geometric', 'numeric'];

// Per requirement: categorical *and* process fields are treated as discrete
// for charting purposes (process values like eg/fl/ag are floats but the
// customer uses them as enumerations). geometric (x/y) and numeric stay
// continuous.
const CATEGORICAL_SECTIONS = new Set(['categorical', 'process']);

// Chart-type radio choices (5 options — facet removed).
const CHART_TYPES = [
  { key: 'scatter', label: '散点图', icon: 'scatter' },
  { key: 'box',     label: '箱型图', icon: 'box' },
  { key: 'violin',  label: '小提琴', icon: 'box' },
  { key: 'swarm',   label: '蜂群图', icon: 'box' },
  { key: 'line',    label: '折线图', icon: 'line' },
  { key: 'wafer',   label: 'Wafer 版图', icon: 'wafer' },
];

// Sentinel for "no facet" radio in wafer mode.
const NO_FACET = '__none__';

// Resolve a field name into an enriched field object (label/unit/section/
// isCategorical) using the metadata from useFields().
function enrichField(name, fields) {
  if (!name || !fields) return null;
  for (const section of SECTION_ORDER) {
    const f = (fields.raw?.[section] || []).find((x) => x.name === name);
    if (f) {
      return {
        name: f.name,
        label: f.label || f.name,
        unit: f.unit || '',
        section,
        isCategorical: CATEGORICAL_SECTIONS.has(section),
      };
    }
  }
  return { name, label: name, unit: '', section: 'other', isCategorical: false };
}

// localStorage key for Explore page state persistence
const EXPLORE_STATE_KEY = 'aln_explore_state_v1';

function loadExploreState() {
  try {
    const raw = localStorage.getItem(EXPLORE_STATE_KEY);
    if (raw) return JSON.parse(raw);
  } catch {
    // ignore corrupt storage
  }
  return null;
}

function saveExploreState(state) {
  try {
    localStorage.setItem(EXPLORE_STATE_KEY, JSON.stringify(state));
  } catch {
    // ignore quota exceeded
  }
}

export default function Explore() {
  const { data: fields, loading: fLoading, error: fErr } = useFields();

  // Load persisted state on mount, fallback to defaults
  const persisted = loadExploreState();

  // Persisted across chart-type switches (don't reset on chartType change).
  const [chartType, setChartType] = useState(persisted?.chartType || 'scatter');
  const [xFields, setXFields] = useState(persisted?.xFields || ['fs_ghz']);
  // Y fields: list of { name, aggregation } where aggregation is one of
  // AGG_OPTIONS keys. Default 'all' = no aggregation (plot raw rows).
  const [yFields, setYFields] = useState(persisted?.yFields || [{ name: 'qs', aggregation: 'all' }]);
  // Z field: { name, aggregation } | null. Same convention as Y.
  const [zField, setZField] = useState(persisted?.zField || null);

  // wafer-only state. waferZ carries an aggregation just like yFields,
  // because multiple devices can share the same (x, y) cell — without
  // aggregation the last-drawn point silently wins.
  const [waferZ, setWaferZ] = useState(persisted?.waferZ || { name: 'k2eff_pct', aggregation: 'mean' });
  const [waferFacet, setWaferFacet] = useState(persisted?.waferFacet || NO_FACET);

  // Filters / query state.
  const [filters, setFilters] = useState(persisted?.filters || {});
  const [limit, setLimit] = useState(50000);
  const [rows, setRows] = useState([]);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [activeDevice, setActiveDevice] = useState(null);
  const [exporting, setExporting] = useState(false);

  // 图上框选隐藏点：纯客户端、跨 chart type 全局。
  //   - hiddenIds:    当前被隐藏的 row.id 集合
  //   - prevHiddenIds: 单步撤销栈（null = 无可撤销）
  //   - shiftHeldRef: 全局 Shift 键状态。Plotly 的 onSelected 事件不带
  //                   modifier，需要我们自己跟。
  const [hiddenIds, setHiddenIds] = useState(() => new Set());
  const [prevHiddenIds, setPrevHiddenIds] = useState(null);
  const shiftHeldRef = useRef(false);

  // Persist toolbar selections to localStorage so they survive tab switches / reloads.
  useEffect(() => {
    saveExploreState({
      chartType,
      xFields,
      yFields,
      zField,
      waferZ,
      waferFacet,
      filters,
    });
  }, [chartType, xFields, yFields, zField, waferZ, waferFacet, filters]);

  useEffect(() => {
    const down = (e) => { if (e.key === 'Shift') shiftHeldRef.current = true; };
    const up   = (e) => { if (e.key === 'Shift') shiftHeldRef.current = false; };
    // 窗口失焦时把 ref 复位 — 否则用户切窗口期间松开 Shift，回来后
    // ref 残留 true 会让下一次"只看"误判成"隐藏"。
    const blur = () => { shiftHeldRef.current = false; };
    window.addEventListener('keydown', down);
    window.addEventListener('keyup', up);
    window.addEventListener('blur', blur);
    return () => {
      window.removeEventListener('keydown', down);
      window.removeEventListener('keyup', up);
      window.removeEventListener('blur', blur);
    };
  }, []);

  const isWafer = chartType === 'wafer';

  // Enriched field metadata for charts. yMeta/zMeta carry the per-field
  // `aggregation` selected by the user (default 'all' = no aggregation).
  const xMeta = useMemo(
    () => xFields.map((n) => enrichField(n, fields)).filter(Boolean),
    [xFields, fields],
  );
  const yMeta = useMemo(
    () =>
      yFields
        .map((y) => {
          const m = enrichField(y.name, fields);
          return m ? { ...m, aggregation: y.aggregation || 'all' } : null;
        })
        .filter(Boolean),
    [yFields, fields],
  );
  const zMeta = useMemo(() => {
    if (!zField) return null;
    const m = enrichField(zField.name, fields);
    return m ? { ...m, aggregation: zField.aggregation || 'all' } : null;
  }, [zField, fields]);
  const waferZMeta = useMemo(() => {
    if (!waferZ) return null;
    const m = enrichField(waferZ.name, fields);
    return m ? { ...m, aggregation: waferZ.aggregation || 'all' } : null;
  }, [waferZ, fields]);

  // Whether the current selection requires the aggregate API.
  const needsAggregate = useMemo(() => {
    if (isWafer) {
      return !!(waferZ && waferZ.aggregation && waferZ.aggregation !== 'all');
    }
    if (yMeta.some((f) => f.isCategorical ? false : (f.aggregation && f.aggregation !== 'all'))) return true;
    if (zMeta && !zMeta.isCategorical && zMeta.aggregation && zMeta.aggregation !== 'all') return true;
    return false;
  }, [isWafer, waferZ, yMeta, zMeta]);

  // Aggregate mode requires at least one categorical X. Wafer mode is
  // exempt because its group_by is always (x, y, [facet]).
  const hasCategoricalX = useMemo(() => xMeta.some((f) => f.isCategorical), [xMeta]);
  const aggregateXWarning = useMemo(() => {
    if (!needsAggregate || isWafer) return null;
    if (!hasCategoricalX) return '聚合模式需要至少一个类别 X 字段（已自动 fallback 为 ALL，未真正聚合）';
    return null;
  }, [needsAggregate, hasCategoricalX, isWafer]);
  // If aggregate would be invalid (no categorical X) we silently fall back.
  const useAggregate = isWafer ? needsAggregate : (needsAggregate && hasCategoricalX);

  // Y/Z conflict: same field with different aggregations would need two
  // metric columns and a renamed Z key. We refuse the combination and
  // tell the user — Z=Y is rarely meaningful anyway (the color encodes
  // the same thing the Y-axis already shows).
  const yzFieldConflict = useMemo(() => {
    if (!zMeta) return null;
    const yHit = yMeta.find((y) => y.name === zMeta.name);
    if (!yHit) return null;
    return `Z 字段 "${zMeta.name}" 与 Y 字段重复，颜色编码与 Y 轴信息一致 — 建议改选其他 Z 字段`;
  }, [yMeta, zMeta]);

  const run = async () => {
    setLoading(true);
    setError(null);
    try {
      // ── Wafer aggregate branch: collapse multiple devices per (x, y) cell.
      if (isWafer && useAggregate) {
        const wireOp = aggWireName(waferZ.aggregation);
        const groupBy = ['x', 'y'];
        if (waferFacet && waferFacet !== NO_FACET) groupBy.push(waferFacet);
        const metrics = [{ field: waferZ.name, agg: [wireOp] }];
        const res = await queryAggregate({ filters, group_by: groupBy, metrics });
        const flat = (res.groups || []).map((g) => {
          const row = { ...g };
          const v = g[waferZ.name];
          if (v && typeof v === 'object' && !Array.isArray(v)) {
            row[waferZ.name] = v[wireOp];
          }
          return row;
        });
        setRows(flat);
        setStats({ total: flat.length, returned: flat.length, truncated: false });
        return;
      }

      // ── Generic aggregate branch (Y/Z aggregation requested + categorical X)
      if (!isWafer && useAggregate) {
        // group_by = selected X fields, plus Z if Z is a categorical bucket
        // (otherwise the per-Z lines / boxes would collapse into the X bin).
        const groupBy = xFields.slice();
        if (zMeta && zMeta.isCategorical && !groupBy.includes(zMeta.name)) {
          groupBy.push(zMeta.name);
        }
        // Build metrics: collect ops per field across Y and (numeric) Z.
        // When Y and Z are the same field with the same op, we just send
        // it once; when ops differ we emit both, but the flatten step
        // below uses Y's op to populate row[field] (Z=Y is flagged in the
        // UI as a conflict — we don't need a second column for it).
        const opsByField = new Map(); // field -> Set<wireOp>
        const yOpByField = {};
        for (const y of yMeta) {
          if (!y.isCategorical && y.aggregation && y.aggregation !== 'all') {
            const op = aggWireName(y.aggregation);
            if (!opsByField.has(y.name)) opsByField.set(y.name, new Set());
            opsByField.get(y.name).add(op);
            yOpByField[y.name] = op;
          }
        }
        let zOp = null;
        if (zMeta && !zMeta.isCategorical && zMeta.aggregation && zMeta.aggregation !== 'all') {
          zOp = aggWireName(zMeta.aggregation);
          if (!opsByField.has(zMeta.name)) opsByField.set(zMeta.name, new Set());
          opsByField.get(zMeta.name).add(zOp);
        }
        const metrics = [...opsByField].map(([field, ops]) => ({ field, agg: [...ops] }));

        const res = await queryAggregate({ filters, group_by: groupBy, metrics });
        // Flatten {x_field: v, qs: {avg: 1234, p75: 5678}} → row[qs] = Y's op,
        // and (when Z is numeric and not also a Y) row[zMeta.name] = Z's op.
        const flat = (res.groups || []).map((g) => {
          const row = {};
          for (const k of Object.keys(g)) {
            const v = g[k];
            if (v && typeof v === 'object' && !Array.isArray(v)) {
              // Pick Y's op for this field if available, else Z's op.
              const op = yOpByField[k] || (k === zMeta?.name ? zOp : null);
              row[k] = op != null ? v[op] : undefined;
            } else {
              row[k] = v;
            }
          }
          // Y fields with aggregation 'all' become null so the chart shows
          // empty cells — a visible signal that the user mixed agg/raw.
          for (const y of yMeta) {
            if ((y.aggregation || 'all') === 'all' && !(y.name in row)) {
              row[y.name] = null;
            }
          }
          return row;
        });
        setRows(flat);
        setStats({ total: flat.length, returned: flat.length, truncated: false });
        return;
      }

      // ── Default branch: raw rows via /api/query/devices.
      const fieldSet = new Set(['id']);
      if (isWafer) {
        ['x', 'y'].forEach((k) => fieldSet.add(k));
        if (waferZ?.name) fieldSet.add(waferZ.name);
        if (waferFacet && waferFacet !== NO_FACET) fieldSet.add(waferFacet);
        // include common identification cols for tooltip / modal.
        ['batch_no', 'wafer', 'folder_name', 'pf'].forEach((k) => fieldSet.add(k));
      } else {
        xFields.forEach((n) => fieldSet.add(n));
        yFields.forEach((y) => fieldSet.add(y.name));
        if (zField) fieldSet.add(zField.name);
        // device-identification cols for the modal.
        ['batch_no', 'wafer', 'folder_name', 'coord', 'pf'].forEach((k) => fieldSet.add(k));
      }
      const res = await queryDevices({
        filters,
        fields: Array.from(fieldSet),
        limit,
        skip_count: true,
      });
      setRows(res.rows || []);
      setStats({
        total: res.total,
        returned: res.returned,
        truncated: res.truncated,
        sampled: res.sampled,
        sample_rate: res.sample_rate,
      });
    } catch (e) {
      setError(e.message);
      // Don't leave stale rows on screen with an error banner — the user
      // would otherwise mistake the previous query's chart for the
      // current (failed) one.
      setRows([]);
      setStats(null);
    } finally {
      setLoading(false);
    }
  };

  const onExportCsv = async () => {
    setExporting(true);
    setError(null);
    try {
      const res = await exportCsv({
        filters, fields: EXPORT_FIELDS, limit: 200000, order_by: 'id',
      });
      const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
      downloadBlob(res.data, `devices_${ts}.csv`);
    } catch (e) {
      setError(e.message || '导出失败');
    } finally {
      setExporting(false);
    }
  };

  // Header title for the chart card.
  const titleText = isWafer
    ? `Wafer 版图 · ${waferZMeta ? displayLabel(waferZMeta) : waferZ?.name || ''}`
      + (waferZ?.aggregation && waferZ.aggregation !== 'all' ? ` (${aggLabel(waferZ.aggregation)})` : '')
    : (() => {
        const xs = xMeta.map((f) => displayLabel(f)).join(', ') || '—';
        const ys = yMeta.map((f) => displayLabel(f)).join(', ') || '—';
        return `${xs}  ×  ${ys}`;
      })();

  // Distinct facet values for wafer mode. Numeric values sort numerically;
  // strings use natural (numeric-aware) compare so wafer-2 < wafer-10.
  const waferFacetValues = useMemo(() => {
    if (!isWafer || !waferFacet || waferFacet === NO_FACET) return [];
    const s = new Set();
    rows.forEach((r) => {
      const v = r[waferFacet];
      if (v !== null && v !== undefined) s.add(v);
    });
    return Array.from(s).sort((a, b) => {
      if (typeof a === 'number' && typeof b === 'number') return a - b;
      return String(a).localeCompare(String(b), undefined, { numeric: true });
    });
  }, [rows, isWafer, waferFacet]);

  // Heuristic warning: violin/box/swarm with no categorical X is awkward.
  const violinXWarning = useMemo(() => {
    if (!(chartType === 'violin' || chartType === 'box' || chartType === 'swarm')) return null;
    if (xMeta.length === 0) return null;
    const allNumeric = xMeta.every((f) => !f.isCategorical);
    if (allNumeric) return '建议至少选一个类别字段做 X（例如 EG / batch_no），否则会被强制按数值分箱';
    return null;
  }, [chartType, xMeta]);

  // Runtime warnings emitted by UnifiedChartGrid (numeric Z dropped, X
  // tick count clamped, etc). Reset on each query.
  const [chartWarnings, setChartWarnings] = useState([]);
  useEffect(() => { setChartWarnings([]); }, [rows, chartType, xFields, yFields, zField]);
  const onChartWarn = useCallback((w) => {
    setChartWarnings((prev) => {
      if (prev.some((p) => p.kind === w.kind && p.xField === w.xField)) return prev;
      return [...prev, w];
    });
  }, []);

  // Format a single warning into a user-facing string.
  const formatChartWarning = (w) => {
    if (w.kind === 'z_numeric_dropped') {
      return `当前图表（散点图）不支持数值 Z（"${w.zField}"）做颜色编码，已忽略 — 请改选类别字段`;
    }
    if (w.kind === 'x_categories_clamped') {
      return `X 字段 "${w.xField}" 共 ${w.total} 个不同值，超出箱型图/小提琴图/蜂群图渲染上限，仅显示前 ${w.shown} 个 — 建议改用散点图或选数值更少的 X`;
    }
    if (w.kind === 'swarm_y_categorical') {
      return `蜂群图暂不支持类别 Y 字段 "${w.yField}" — 请切到箱型图/小提琴图`;
    }
    return JSON.stringify(w);
  };

  // ── 图上框选隐藏点：派生与 handler ──────────────────────────────────
  // visibleRows = rows 中 id 不在 hiddenIds 内的子集。hiddenIds 空时直接
  // 复用 rows 引用，避免重复 filter 触发下游 useMemo。
  const visibleRows = useMemo(
    () => (hiddenIds.size === 0 ? rows : rows.filter((r) => !hiddenIds.has(r.id))),
    [rows, hiddenIds],
  );
  // 顶部小条显示的计数：只算当前 rows 里实际命中的，避免 stale id（filters
  // 改过后 hiddenIds 里有些 id 已经不在 rows 里）让用户误以为还隐藏着很多。
  const hiddenInCurrentRows = useMemo(
    () => (hiddenIds.size === 0 ? 0 : rows.reduce((n, r) => n + (hiddenIds.has(r.id) ? 1 : 0), 0)),
    [rows, hiddenIds],
  );

  const handleSelection = useCallback((e) => {
    if (!e || !Array.isArray(e.points)) return;
    const selectedIds = new Set();
    for (const p of e.points) {
      const row = p.customdata;
      // 聚合 trace（box/violin/line 的统计点）没有原始 row 或缺 id，跳过。
      // aggregated 模式（wafer 聚合 / Y/Z 聚合）的 row 也无 id，自动 no-op。
      if (row && row.id != null) selectedIds.add(row.id);
    }
    if (selectedIds.size === 0) return;

    const shift = shiftHeldRef.current;
    let next;
    if (shift) {
      // Shift+拖 = 隐藏选中
      next = new Set(hiddenIds);
      for (const id of selectedIds) next.add(id);
    } else {
      // 默认拖 = 只看选中。等价于把当前 visible 里未选中的全部加入 hidden。
      // 防御：如果 visible 里没有一行命中 selectedIds（例如选区完全是
      // box/violin 的均值点），别误把所有 visible 都隐藏掉。
      const remainingVisible = visibleRows.reduce(
        (n, r) => n + (selectedIds.has(r.id) ? 1 : 0),
        0,
      );
      if (remainingVisible === 0) return;
      next = new Set(hiddenIds);
      for (const r of visibleRows) {
        if (!selectedIds.has(r.id)) next.add(r.id);
      }
    }

    // no-op 检测：next 与 hiddenIds 完全一致就跳过（避免污染撤销栈）。
    if (next.size === hiddenIds.size) {
      let same = true;
      for (const id of next) if (!hiddenIds.has(id)) { same = false; break; }
      if (same) return;
    }

    setPrevHiddenIds(hiddenIds);
    setHiddenIds(next);
  }, [hiddenIds, visibleRows]);

  const handleUndo = useCallback(() => {
    if (prevHiddenIds == null) return;
    setHiddenIds(prevHiddenIds);
    setPrevHiddenIds(null);
  }, [prevHiddenIds]);
  const handleClear = useCallback(() => {
    if (hiddenIds.size === 0) return;
    setPrevHiddenIds(hiddenIds);
    setHiddenIds(new Set());
  }, [hiddenIds]);
  const handleInvert = useCallback(() => {
    // 反选保留：现在看见的 → 隐藏；现在隐藏的（且在 rows 里）→ 露出。
    // hiddenIds 里的 stale id（不在 rows）按反选语义被丢弃。
    if (visibleRows.length === 0 && hiddenIds.size === 0) return;
    const next = new Set();
    for (const r of visibleRows) next.add(r.id);
    setPrevHiddenIds(hiddenIds);
    setHiddenIds(next);
  }, [visibleRows, hiddenIds]);

  const handlePointClick = useCallback((d) => setActiveDevice(d), []);
  const handleCloseDevice = useCallback(() => setActiveDevice(null), []);

  return (
    <>
      <div className="toolbar">
        <span className="crumb">
          谐振器 <span style={{ color: 'var(--fg-4)' }}>›</span> <b>数据分析</b>
        </span>
        <div className="divider" />
        <div className="group">
          {CHART_TYPES.map((c) => {
            const Icn = I[c.icon] || I.scatter;
            return (
              <button
                key={c.key}
                className={chartType === c.key ? 'active' : ''}
                onClick={() => setChartType(c.key)}
                title={c.label}
              >
                <Icn size={14} /> {c.label}
              </button>
            );
          })}
        </div>
        <div className="spacer" />
        <button className="btn" onClick={onExportCsv} disabled={exporting} title="按当前筛选条件导出为 CSV">
          <I.download size={13} /> {exporting ? '导出中…' : '导出 CSV'}
        </button>
        <button className="btn" disabled title="敬请期待">
          <I.download size={13} /> 导出 Excel
        </button>
        <button
          className="btn primary"
          onClick={run}
          disabled={loading || fLoading || (!isWafer && xFields.length === 0)}
          title={!isWafer && xFields.length === 0 ? '请先选择 X 字段' : undefined}
        >
          <I.refresh size={13} /> {loading ? '查询中...' : '运行查询'}
        </button>
      </div>

      <div className="workspace">
        <FilterPanel value={filters} onApply={setFilters} />
        <div className="canvas-wrap">
          <div className="chart-card">
            <div className="chart-head">
              <span className="title">{titleText}</span>
              <span className="axes">
                {stats
                  ? `${stats.returned}/${stats.total} 行${stats.truncated ? ' · 已截断' : ''}${stats.sampled ? ' · 已采样 (' + Math.round(stats.sample_rate * 100) + '%)' : ''}`
                  : '尚未查询'}
              </span>
            </div>
            <div className="chart-body" style={{ minHeight: 480 }}>
              {fErr && (
                <div style={{ padding: 14, color: 'var(--fail)' }}>
                  字段加载失败：{fErr.message}
                </div>
              )}
              {error && (
                <div style={{ padding: 14, color: 'var(--fail)' }}>
                  <I.alert size={12} /> {error}
                </div>
              )}
              {!error && rows.length === 0 && !loading && (
                <div style={{ padding: 40, color: 'var(--fg-4)', textAlign: 'center' }}>
                  请配置参数后点击「运行查询」
                </div>
              )}
              {rows.length > 0 && (
                <div style={{ position: 'absolute', inset: 0, overflow: 'auto' }}>
                  {/* Runtime warnings: aggregate fallback / Y=Z conflict /
                      chart-type-specific issues. Always shown above the chart
                      so the user notices when something silently changed. */}
                  {(aggregateXWarning || yzFieldConflict || chartWarnings.length > 0) && (
                    <div className="explore-chartwarns" style={{
                      padding: '8px 12px',
                      background: 'rgba(255, 195, 0, 0.08)',
                      borderBottom: '1px solid rgba(255, 195, 0, 0.3)',
                      fontSize: 11.5,
                      color: '#92611a',
                    }}>
                      {aggregateXWarning && <div>⚠ {aggregateXWarning}</div>}
                      {yzFieldConflict && <div>⚠ {yzFieldConflict}</div>}
                      {chartWarnings.map((w, i) => (
                        <div key={i}>⚠ {formatChartWarning(w)}</div>
                      ))}
                    </div>
                  )}
                  {hiddenIds.size > 0 && (
                    <div className="explore-hiddenbar" style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 10,
                      padding: '6px 12px',
                      background: 'rgba(44, 121, 246, 0.06)',
                      borderBottom: '1px solid rgba(44, 121, 246, 0.25)',
                      fontSize: 11.5,
                      color: '#1e3a5f',
                    }}>
                      <span style={{ fontWeight: 600 }}>
                        已隐藏 {hiddenInCurrentRows} 个点
                      </span>
                      <button
                        className="btn"
                        style={{ padding: '2px 8px', fontSize: 11 }}
                        onClick={handleInvert}
                        title="把当前可见的点反过来隐藏（露出之前隐藏的）"
                      >
                        反选保留
                      </button>
                      <button
                        className="btn"
                        style={{ padding: '2px 8px', fontSize: 11 }}
                        onClick={handleUndo}
                        disabled={prevHiddenIds == null}
                        title="撤销上一步隐藏操作"
                      >
                        撤销
                      </button>
                      <button
                        className="btn"
                        style={{ padding: '2px 8px', fontSize: 11 }}
                        onClick={handleClear}
                        title="清空所有隐藏，显示全部"
                      >
                        清空
                      </button>
                      <span style={{ marginLeft: 'auto', color: 'var(--fg-4)', fontSize: 11 }}>
                        提示：先点工具条 lasso / 框选；拖 = 只看选中，Shift+拖 = 隐藏选中
                      </span>
                    </div>
                  )}
                  {!isWafer && (xMeta.length === 0 || yMeta.length === 0) && (
                    <div style={{ padding: 40, color: 'var(--fg-4)', textAlign: 'center' }}>
                      请至少选择 1 个 X 字段和 1 个 Y 字段
                    </div>
                  )}
                  {!isWafer && xMeta.length > 0 && yMeta.length > 0 && (
                    <UnifiedChartGrid
                      chartType={chartType}
                      rows={visibleRows}
                      xFields={xMeta}
                      yFields={yMeta}
                      zField={zMeta}
                      onWarn={onChartWarn}
                      onPointClick={handlePointClick}
                      onSelection={handleSelection}
                    />
                  )}
                  {isWafer && (
                    <WaferMap
                      rows={visibleRows}
                      valueField={waferZ.name}
                      valueLabel={waferZMeta ? displayLabel(waferZMeta) : waferZ.name}
                      facetField={waferFacet !== NO_FACET ? waferFacet : null}
                      facets={waferFacetValues}
                      aggregated={useAggregate}
                      // 聚合模式下一格已合并多器件，没有 device.id 可跳详情；
                      // 不传 onPointClick 让 WaferMap 内部禁用点击。
                      onPointClick={useAggregate ? undefined : handlePointClick}
                      onSelection={handleSelection}
                    />
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
        <Inspector
          fields={fields}
          chartType={chartType}
          xFields={xFields}
          setXFields={setXFields}
          yFields={yFields}
          setYFields={setYFields}
          zField={zField}
          setZField={setZField}
          waferZ={waferZ}
          setWaferZ={setWaferZ}
          waferFacet={waferFacet}
          setWaferFacet={setWaferFacet}
          xMeta={xMeta}
          yMeta={yMeta}
          violinXWarning={violinXWarning}
          aggregateXWarning={aggregateXWarning}
          yzFieldConflict={yzFieldConflict}
          limit={limit}
          setLimit={setLimit}
          stats={stats}
        />
      </div>

      {activeDevice && <DeviceModal device={activeDevice} onClose={handleCloseDevice} />}
    </>
  );
}

/* -------------------------------------------------------------------------
 * Inspector — right-rail config panel.
 *
 * In normal modes it shows multi-select X / multi-select Y / single-select Z.
 * In wafer mode X/Y are locked and only Z (numeric only) + facet (categorical
 * only) are exposed.
 * ----------------------------------------------------------------------- */
function Inspector({
  fields, chartType,
  xFields, setXFields,
  yFields, setYFields,
  zField, setZField,
  waferZ, setWaferZ,
  waferFacet, setWaferFacet,
  xMeta, yMeta, violinXWarning, aggregateXWarning, yzFieldConflict,
  limit, setLimit, stats,
}) {
  const isWafer = chartType === 'wafer';

  // Grid preview text (X cols × Y rows).
  const gridText = `将渲染 ${yMeta.length || '?'} 行 × ${xMeta.length || '?'} 列 = ${(yMeta.length || 0) * (xMeta.length || 0) || '?'} 个子图`;

  return (
    <div className="panel right">
      <div className="panel-head">
        <I.settings size={12} />
        <span>图表配置</span>
      </div>
      <div className="panel-body">
        {isWafer ? (
          <>
            <div className="section">
              <div className="section-title">轴 / 编码</div>
              <div className="explore-locked-hint">
                X / Y 锁定为器件几何坐标 (x, y)
              </div>
              <div className="field-label" style={{ marginBottom: 6 }}>
                <span>颜色编码 (Z)</span>
                <span className="hint">同 (x,y) 多值时按聚合方式合并</span>
              </div>
              <FieldRadioWithAgg
                hint="数值"
                fields={fields}
                value={waferZ}
                onChange={(v) => v && setWaferZ(v)}
                allowedSections={['numeric', 'process', 'geometric']}
                allowNone={false}
              />
              <FieldRadio
                label="分面字段"
                hint="可选 · 类别"
                fields={fields}
                value={waferFacet}
                onChange={setWaferFacet}
                allowedSections={['categorical', 'process']}
                allowNone={true}
                noneLabel="不分面"
                noneValue="__none__"
              />
            </div>
          </>
        ) : (
          <>
            <div className="section">
              <div className="section-title">X 字段（可多选）</div>
              <FieldCheckList
                fields={fields}
                value={xFields}
                onChange={setXFields}
              />
            </div>
            <div className="section">
              <div className="section-title" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span>Y 字段（可多选）</span>
                {xFields.length === 0 && (
                  <span className="hint">请先选择 X 字段</span>
                )}
              </div>
              <FieldCheckListWithAgg
                fields={fields}
                value={yFields}
                onChange={setYFields}
                disabled={xFields.length === 0}
              />
              {/* aggregate fallback wins over violin/box X hint —
                  the two messages overlap and contradict each other. */}
              {aggregateXWarning ? (
                <div className="explore-warn">⚠ {aggregateXWarning}</div>
              ) : violinXWarning ? (
                <div className="explore-warn">⚠ {violinXWarning}</div>
              ) : null}
              {yzFieldConflict && (
                <div className="explore-warn">⚠ {yzFieldConflict}</div>
              )}
            </div>
            <div className="section">
              <div className="section-title">颜色 / 分组（Z，单选）</div>
              <FieldRadioWithAgg
                hint="可选"
                fields={fields}
                value={zField}
                onChange={setZField}
                allowedSections={['categorical', 'process', 'numeric', 'geometric']}
                allowNone={true}
                noneLabel="不编码"
              />
            </div>
            <div className="explore-grid-hint">{gridText}</div>
          </>
        )}

        <div className="section">
          <div className="section-title">查询参数</div>
          <div className="field">
            <div className="field-label">
              <span>返回上限</span>
            </div>
            <input
              className="input mono"
              type="number"
              min={1}
              max={200000}
              value={limit}
              onChange={(e) => {
                const n = parseInt(e.target.value, 10);
                // 用户清空输入框时回退默认值，避免发出 limit=0 触发后端 400。
                setLimit(Number.isFinite(n) && n >= 1 ? Math.min(n, 200000) : 50000);
              }}
            />
          </div>
        </div>
        {stats && (
          <div className="section">
            <div className="section-title">查询统计</div>
            <div
              style={{
                background: 'var(--bg-panel-2)',
                border: '1px solid var(--border)',
                borderRadius: 4,
                padding: 8,
                fontFamily: 'var(--font-mono)',
                fontSize: 11.5,
                lineHeight: 1.7,
              }}
            >
              <div className="row-flex" style={{ justifyContent: 'space-between' }}>
                <span className="muted">总数</span>
                <span>{stats.total}</span>
              </div>
              <div className="row-flex" style={{ justifyContent: 'space-between' }}>
                <span className="muted">返回</span>
                <span>{stats.returned}</span>
              </div>
              <div className="row-flex" style={{ justifyContent: 'space-between' }}>
                <span className="muted">截断</span>
                <span style={{ color: stats.truncated ? 'var(--fail)' : 'var(--pass)' }}>
                  {stats.truncated ? '是' : '否'}
                </span>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------
 * FieldCheckList — multi-select checkbox grid grouped by section.
 *
 * Lays sections out as collapsible labelled rows of checkboxes (categorical /
 * process / geometric / numeric). Selected items are toggled in `value`
 * (an array of field-names) via onChange.
 * ----------------------------------------------------------------------- */
function FieldCheckList({ fields, value, onChange, discouragedSections = [], discouragedHint }) {
  const selected = useMemo(() => new Set(value), [value]);
  const toggle = (name) => {
    if (selected.has(name)) onChange(value.filter((v) => v !== name));
    else onChange([...value, name]);
  };
  if (!fields) return <div className="dim" style={{ fontSize: 11 }}>加载中…</div>;
  const discouragedSet = new Set(discouragedSections);
  return (
    <div className="explore-fieldlist">
      {SECTION_ORDER.map((section) => {
        const items = fields.raw?.[section] || [];
        if (items.length === 0) return null;
        const isDiscouraged = discouragedSet.has(section);
        return (
          <div key={section} className="explore-fieldgroup">
            <div className="explore-fieldgroup-head">
              <span className="explore-fieldgroup-name">{SECTION_LABELS[section] || section}</span>
              {isDiscouraged && discouragedHint && (
                <span className="explore-fieldgroup-warn">{discouragedHint}</span>
              )}
            </div>
            <div className="explore-fieldgroup-body">
              {items.map((f) => {
                const checked = selected.has(f.name);
                return (
                  <label
                    key={f.name}
                    className={`explore-fieldchip${checked ? ' checked' : ''}${isDiscouraged ? ' discouraged' : ''}`}
                    title={displayLabel(f)}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggle(f.name)}
                      style={{ position: 'absolute', opacity: 0, pointerEvents: 'none' }}
                    />
                    <span className="explore-fieldchip-cb" aria-hidden>
                      {checked && <I.check size={9} stroke="#fff" sw={2.5} />}
                    </span>
                    <span className="explore-fieldchip-label">{displayLabel(f)}</span>
                  </label>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* -------------------------------------------------------------------------
 * FieldCheckListWithAgg — like FieldCheckList but each numeric field carries
 * an aggregation `<select>` (ALL / max / min / mean / median / p25 / p75).
 *
 * value:    [{ name, aggregation }, ...]
 * onChange: receives a new array.
 *
 * Categorical sections never show the dropdown — aggregation is meaningless
 * for category labels.
 * ----------------------------------------------------------------------- */
function FieldCheckListWithAgg({ fields, value, onChange, disabled = false }) {
  const byName = useMemo(() => {
    const m = new Map();
    for (const v of value) m.set(v.name, v);
    return m;
  }, [value]);
  if (!fields) return <div className="dim" style={{ fontSize: 11 }}>加载中…</div>;
  const toggle = (name) => {
    if (byName.has(name)) onChange(value.filter((v) => v.name !== name));
    else onChange([...value, { name, aggregation: 'all' }]);
  };
  const setAgg = (name, agg) => {
    onChange(value.map((v) => (v.name === name ? { ...v, aggregation: agg } : v)));
  };
  return (
    <div className="explore-fieldlist">
      {SECTION_ORDER.map((section) => {
        const items = fields.raw?.[section] || [];
        if (items.length === 0) return null;
        const isCategorical = CATEGORICAL_SECTIONS.has(section);
        return (
          <div key={section} className="explore-fieldgroup">
            <div className="explore-fieldgroup-head">
              <span className="explore-fieldgroup-name">{SECTION_LABELS[section] || section}</span>
            </div>
            <div className="explore-fieldgroup-body">
              {items.map((f) => {
                const entry = byName.get(f.name);
                const checked = !!entry;
                return (
                  <span
                    key={f.name}
                    style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}
                  >
                    <label
                      className={`explore-fieldchip${checked ? ' checked' : ''}`}
                      title={displayLabel(f)}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggle(f.name)}
                        style={{ position: 'absolute', opacity: 0, pointerEvents: 'none' }}
                      />
                      <span className="explore-fieldchip-cb" aria-hidden>
                        {checked && <I.check size={9} stroke="#fff" sw={2.5} />}
                      </span>
                      <span className="explore-fieldchip-label">{displayLabel(f)}</span>
                    </label>
                    {checked && !isCategorical && (
                      <select
                        className="input mono"
                        value={entry.aggregation || 'all'}
                        onChange={(e) => setAgg(f.name, e.target.value)}
                        style={{
                          height: 22,
                          padding: '0 4px',
                          fontSize: 10.5,
                          width: 'auto',
                          minWidth: 64,
                        }}
                        title="聚合方式：不聚合=原始值，其他=按 X 分组聚合"
                      >
                        {AGG_OPTIONS.map((o) => (
                          <option key={o.key} value={o.key}>{o.label}</option>
                        ))}
                      </select>
                    )}
                  </span>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* -------------------------------------------------------------------------
 * FieldRadioWithAgg — single-select Z field with aggregation dropdown for
 * numeric picks.
 *
 * value:    { name, aggregation } | null
 * onChange: receives the new value (object) or null.
 * ----------------------------------------------------------------------- */
function FieldRadioWithAgg({
  hint, fields, value, onChange,
  allowedSections, allowNone = true, noneLabel = '不编码',
}) {
  if (!fields) return <div className="dim" style={{ fontSize: 11 }}>加载中…</div>;
  const allowed = new Set(allowedSections);
  const currentName = value ? value.name : null;
  const setAgg = (agg) => {
    if (!value) return;
    onChange({ ...value, aggregation: agg });
  };
  // Look up section of currently-selected field to decide whether to show agg.
  const currentSection = useMemo(() => {
    if (!currentName) return null;
    for (const s of SECTION_ORDER) {
      if ((fields.raw?.[s] || []).some((x) => x.name === currentName)) return s;
    }
    return null;
  }, [currentName, fields]);
  const showAgg = currentName && currentSection && !CATEGORICAL_SECTIONS.has(currentSection);
  return (
    <div className="explore-radio">
      {hint && (
        <div className="field-label" style={{ marginBottom: 6 }}>
          <span className="hint">{hint}</span>
        </div>
      )}
      {showAgg && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
          <span style={{ fontSize: 10.5, color: 'var(--fg-3)' }}>聚合：</span>
          <select
            className="input mono"
            value={value.aggregation || 'all'}
            onChange={(e) => setAgg(e.target.value)}
            style={{ height: 22, padding: '0 4px', fontSize: 10.5, minWidth: 110 }}
          >
            {AGG_OPTIONS.map((o) => (
              <option key={o.key} value={o.key}>{o.label}</option>
            ))}
          </select>
        </div>
      )}
      <div className="explore-fieldlist compact">
        {allowNone && (
          <div className="explore-fieldgroup">
            <div className="explore-fieldgroup-body">
              <label className={`explore-fieldchip${value === null ? ' checked' : ''} radio`}>
                <input
                  type="radio"
                  checked={value === null}
                  onChange={() => onChange(null)}
                  style={{ position: 'absolute', opacity: 0, pointerEvents: 'none' }}
                />
                <span className="explore-fieldchip-radio" aria-hidden>
                  {value === null && <span className="dot" />}
                </span>
                <span className="explore-fieldchip-label">{noneLabel}</span>
              </label>
            </div>
          </div>
        )}
        {SECTION_ORDER.filter((s) => allowed.has(s)).map((section) => {
          const items = fields.raw?.[section] || [];
          if (items.length === 0) return null;
          return (
            <div key={section} className="explore-fieldgroup">
              <div className="explore-fieldgroup-head">
                <span className="explore-fieldgroup-name">{SECTION_LABELS[section] || section}</span>
              </div>
              <div className="explore-fieldgroup-body">
                {items.map((f) => {
                  const checked = currentName === f.name;
                  return (
                    <label
                      key={f.name}
                      className={`explore-fieldchip${checked ? ' checked' : ''} radio`}
                      title={displayLabel(f)}
                    >
                      <input
                        type="radio"
                        checked={checked}
                        onChange={() => onChange({ name: f.name, aggregation: 'all' })}
                        style={{ position: 'absolute', opacity: 0, pointerEvents: 'none' }}
                      />
                      <span className="explore-fieldchip-radio" aria-hidden>
                        {checked && <span className="dot" />}
                      </span>
                      <span className="explore-fieldchip-label">{displayLabel(f)}</span>
                    </label>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------
 * FieldRadio — single-select radio grid grouped by section. Used for Z and
 * the wafer-mode pickers. Optionally includes a leading "no encoding" radio.
 * ----------------------------------------------------------------------- */
function FieldRadio({
  label, hint, fields, value, onChange,
  allowedSections, allowNone = false, noneLabel = '不编码', noneValue = null,
}) {
  if (!fields) return <div className="dim" style={{ fontSize: 11 }}>加载中…</div>;
  const allowed = new Set(allowedSections);
  return (
    <div className="explore-radio">
      {label && (
        <div className="field-label" style={{ marginBottom: 6 }}>
          <span>{label}</span>
          {hint && <span className="hint">{hint}</span>}
        </div>
      )}
      <div className="explore-fieldlist compact">
        {allowNone && (
          <div className="explore-fieldgroup">
            <div className="explore-fieldgroup-body">
              <label
                className={`explore-fieldchip${value === noneValue ? ' checked' : ''} radio`}
              >
                <input
                  type="radio"
                  checked={value === noneValue}
                  onChange={() => onChange(noneValue)}
                  style={{ position: 'absolute', opacity: 0, pointerEvents: 'none' }}
                />
                <span className="explore-fieldchip-radio" aria-hidden>
                  {value === noneValue && <span className="dot" />}
                </span>
                <span className="explore-fieldchip-label">{noneLabel}</span>
              </label>
            </div>
          </div>
        )}
        {SECTION_ORDER.filter((s) => allowed.has(s)).map((section) => {
          const items = fields.raw?.[section] || [];
          if (items.length === 0) return null;
          return (
            <div key={section} className="explore-fieldgroup">
              <div className="explore-fieldgroup-head">
                <span className="explore-fieldgroup-name">{SECTION_LABELS[section] || section}</span>
              </div>
              <div className="explore-fieldgroup-body">
                {items.map((f) => {
                  const checked = value === f.name;
                  return (
                    <label
                      key={f.name}
                      className={`explore-fieldchip${checked ? ' checked' : ''} radio`}
                      title={displayLabel(f)}
                    >
                      <input
                        type="radio"
                        checked={checked}
                        onChange={() => onChange(f.name)}
                        style={{ position: 'absolute', opacity: 0, pointerEvents: 'none' }}
                      />
                      <span className="explore-fieldchip-radio" aria-hidden>
                        {checked && <span className="dot" />}
                      </span>
                      <span className="explore-fieldchip-label">{displayLabel(f)}</span>
                    </label>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
