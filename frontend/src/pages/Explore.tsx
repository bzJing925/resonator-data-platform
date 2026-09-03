import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import I from '../components/Icons';
import { UnifiedChartGrid, WaferMap } from '../components/Charts';
import useFields, { displayLabel } from '../hooks/useFields';
import { queryDevices, queryAggregate } from '../api/endpoints';
import DeviceModal from '../components/DeviceModal';
import FilterPanel from '../components/FilterPanel';
import { usePageState } from '../contexts/PageStateContext';
import WorksheetGrid from '../features/analysis/WorksheetGrid';
import WorkspaceTabs from '../features/analysis/WorkspaceTabs';
import { useAnalysisWorkspace } from '../features/analysis/workspace';
import { serializeWorksheetCsv } from '../features/analysis/csv';

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

// Device structure parameters derived from the mapping (对照表).
// These are the only fields users may select as the X axis in Explore.
const X_AXIS_FIELD_NAMES = [
  'structureType', 'eg', 'fl', 'ag', 'pf', 'area_um2', 'area_n',
];
const Y_AXIS_FIELD_NAMES = [
  'fs_ghz', 'fp_ghz', 'qs', 'qp', 'k2eff_pct', 'zs_ohm', 'zp_ohm', 'dbqs', 'dbqp',
];
const X_AXIS_FIELD_SET = new Set(X_AXIS_FIELD_NAMES);
const Y_AXIS_FIELD_SET = new Set(Y_AXIS_FIELD_NAMES);
const AXIS_FIELD_LABELS = {
  structureType: '结构类别',
  eg: 'EG',
  fl: 'FL',
  ag: 'AG',
  pf: 'PF',
  area_um2: '面积',
  area_n: '区域编号',
  batch_no: '批次',
  wafer: 'Wafer',
  x: 'X',
  y: 'Y',
  fs_ghz: 'fs(GHz)',
  fp_ghz: 'fp(GHz)',
  qs: 'Qs',
  qp: 'Qp',
  k2eff_pct: 'k²eff(%)',
  zs_ohm: 'Zs (Ω)',
  zp_ohm: 'Zp (Ω)',
  dbqs: 'dBQs',
  dbqp: 'dBQp',
  fbode_ghz: 'fBode(GHz)',
  qs_bodeq: 'bodeQs',
  qp_bodeq: 'bodeQp',
  fp2_ghz: 'fp2(GHz)',
  fs2_ghz: 'fs2(GHz)',
};

function axisPickerLabel(field) {
  return AXIS_FIELD_LABELS[field.name] || displayLabel(field);
}

function axisFieldOptions(fields, names) {
  if (!fields) return null;
  return names
    .map((name) => name === 'structureType'
      ? { name, label: '结构类别', section: 'derived' }
      : fields.byName?.[name])
    .filter(Boolean)
    .map((f) => ({ ...f, label: AXIS_FIELD_LABELS[f.name] || f.label || f.name }));
}

function normalizeAxisSelection(value, allowedSet, fallback) {
  if (!Array.isArray(value)) return fallback;
  if (value.length === 0) return [];
  const kept = value.filter((name) => allowedSet.has(name));
  return kept.length > 0 ? kept : fallback;
}

function normalizeYSelection(value, allowedSet, fallback) {
  if (!Array.isArray(value)) return fallback;
  if (value.length === 0) return [];
  const kept = value
    .map((item) => {
      const name = typeof item === 'string' ? item : item?.name;
      if (!name || !allowedSet.has(name)) return null;
      return {
        name,
        aggregation: typeof item === 'object' && item?.aggregation ? item.aggregation : 'all',
      };
    })
    .filter(Boolean);
  return kept.length > 0 ? kept : fallback;
}

// Chart-type radio choices (5 options — facet removed).
const CHART_TYPES = [
  { key: 'scatter', label: '散点图', icon: 'scatter' },
  { key: 'box',     label: '箱型图', icon: 'box' },
  { key: 'violin',  label: '小提琴', icon: 'box' },
  { key: 'swarm',   label: '蜂群图', icon: 'box' },
  { key: 'line',    label: '折线图', icon: 'line' },
  { key: 'wafer',   label: 'Wafer 版图', icon: 'wafer' },
];

// Z 展示方式：facet = 每个 Z 值一组竖排子图；color = 原颜色编码。
const Z_MODE_OPTIONS = [
  { key: 'facet', label: '按值分图' },
  { key: 'color', label: '颜色编码（原效果）' },
];

// Sentinel for "no facet" radio in wafer mode.
const NO_FACET = '__none__';
const EMPTY_SELECTION = new Set<string>();

// Resolve a field name into an enriched field object (label/unit/section/
// isCategorical) using the metadata from useFields().
function enrichField(name, fields) {
  if (name === 'structureType') {
    return { name, label: '结构类别', unit: '', section: 'derived', isCategorical: true };
  }
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

const EXPLORE_INITIAL_STATE = {
  chartType: 'scatter',
  xFields: ['eg'],
  yFields: [{ name: 'qs', aggregation: 'all' }],
  zField: null,
  zMode: 'facet',
  waferZ: { name: 'k2eff_pct', aggregation: 'mean' },
  waferFacet: NO_FACET,
  filters: {},
  limit: 50000,
};

export default function Explore() {
  const { data: fields, loading: fLoading, error: fErr } = useFields();
  const {
    state: workspaceState,
    chartDataset,
    activeDataset,
    loadQueryRows,
    duplicateDataset,
    setActiveTab,
    setChartDataset,
    setSelection,
    editCell,
    deleteSelection,
    undo,
    redo,
  } = useAnalysisWorkspace();

  const [rawState, setRawState] = usePageState('explore', EXPLORE_INITIAL_STATE);
  const migratedRowsRef = useRef(false);

  useEffect(() => {
    if (migratedRowsRef.current) return;
    migratedRowsRef.current = true;
    if (Array.isArray(rawState.rows) && rawState.rows.length > 0
      && workspaceState.datasets.length === 0) {
      loadQueryRows(rawState.rows, rawState.stats || null);
    }
    if ('rows' in rawState || 'hiddenIds' in rawState || 'prevHiddenIds' in rawState || 'stats' in rawState) {
      setRawState(({ rows: _rows, hiddenIds: _hidden, prevHiddenIds: _prev, stats: _stats, ...rest }) => rest);
    }
  }, [loadQueryRows, rawState, setRawState, workspaceState.datasets.length]);

  // Normalize persisted axis selections against the current allowed field set.
  const state = useMemo(() => ({
    ...rawState,
    zMode: rawState.zMode === 'color' ? 'color' : 'facet',
    xFields: normalizeAxisSelection(
      rawState.xFields,
      X_AXIS_FIELD_SET,
      EXPLORE_INITIAL_STATE.xFields,
    ),
    yFields: normalizeYSelection(
      rawState.yFields,
      Y_AXIS_FIELD_SET,
      EXPLORE_INITIAL_STATE.yFields,
    ),
  }), [rawState]);

  const {
    chartType,
    xFields,
    yFields,
    zField,
    zMode,
    waferZ,
    waferFacet,
    filters,
    limit,
  } = state;
  const rows = chartDataset?.rows || [];
  const stats = chartDataset?.stats || null;

  const setChartType = useCallback((v) => setRawState((s) => ({ ...s, chartType: typeof v === 'function' ? v(s.chartType) : v })), [setRawState]);
  const setXFields = useCallback((v) => setRawState((s) => ({ ...s, xFields: typeof v === 'function' ? v(s.xFields) : v })), [setRawState]);
  const setYFields = useCallback((v) => setRawState((s) => ({ ...s, yFields: typeof v === 'function' ? v(s.yFields) : v })), [setRawState]);
  const setZField = useCallback((v) => setRawState((s) => ({ ...s, zField: typeof v === 'function' ? v(s.zField) : v })), [setRawState]);
  const setZMode = useCallback((v) => setRawState((s) => ({ ...s, zMode: typeof v === 'function' ? v(s.zMode) : v })), [setRawState]);
  const setWaferZ = useCallback((v) => setRawState((s) => ({ ...s, waferZ: typeof v === 'function' ? v(s.waferZ) : v })), [setRawState]);
  const setWaferFacet = useCallback((v) => setRawState((s) => ({ ...s, waferFacet: typeof v === 'function' ? v(s.waferFacet) : v })), [setRawState]);
  const setFilters = useCallback((v) => setRawState((s) => ({ ...s, filters: typeof v === 'function' ? v(s.filters) : v })), [setRawState]);
  const setLimit = useCallback((v) => setRawState((s) => ({ ...s, limit: typeof v === 'function' ? v(s.limit) : v })), [setRawState]);

  // Transient UI state that does not need to survive route switches.
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [activeDevice, setActiveDevice] = useState(null);
  const [exporting, setExporting] = useState(false);
  const [showFilters, setShowFilters] = useState(true);
  const [showInspector, setShowInspector] = useState(true);
  const [fullscreen, setFullscreen] = useState(false);

  useEffect(() => {
    const app = document.querySelector('.app');
    app?.classList.toggle('analysis-fullscreen', fullscreen);
    return () => app?.classList.remove('analysis-fullscreen');
  }, [fullscreen]);

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

  // 分面生效条件：用户选了「按值分图」且 Z 是类别字段（数值 Z 永不进入分面）。
  const faceting = zMode === 'facet' && !!zMeta?.isCategorical;
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
  const hasDerivedX = xFields.includes('structureType');
  const aggregateXWarning = useMemo(() => {
    if (!needsAggregate || isWafer) return null;
    if (hasDerivedX) return '工作表派生列暂不参与后端聚合；本次查询按原始行加载';
    if (!hasCategoricalX) return '聚合模式需要至少一个类别 X 字段（已自动 fallback 为 ALL，未真正聚合）';
    return null;
  }, [needsAggregate, hasCategoricalX, hasDerivedX, isWafer]);
  // If aggregate would be invalid (no categorical X) we silently fall back.
  const useAggregate = isWafer ? needsAggregate : (needsAggregate && hasCategoricalX && !hasDerivedX);

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
    setChartWarnings([]);
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
        loadQueryRows(flat, { total: flat.length, returned: flat.length, truncated: false });
        return;
      }

      // ── Generic aggregate branch (Y/Z aggregation requested + categorical X)
      if (!isWafer && useAggregate) {
        // group_by = selected X fields, plus Z if Z is a categorical bucket
        // (otherwise the per-Z lines / boxes would collapse into the X bin).
        const groupBy = xFields.filter((name) => name !== 'structureType');
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
        loadQueryRows(flat, { total: flat.length, returned: flat.length, truncated: false });
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
        xFields.forEach((n) => { if (n !== 'structureType') fieldSet.add(n); });
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
        sampling: 'none',
      });
      loadQueryRows(res.rows || [], {
        total: res.total,
        returned: res.returned,
        truncated: res.truncated,
        sampled: res.sampled,
        sample_rate: res.sample_rate,
      });
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const onExportCsv = async () => {
    if (!chartDataset) return;
    setExporting(true);
    setError(null);
    try {
      const blob = new Blob([serializeWorksheetCsv(chartDataset.rows)], {
        type: 'text/csv;charset=utf-8',
      });
      const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
      downloadBlob(blob, `${chartDataset.name}_${ts}.csv`);
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
  // tick count clamped, etc). 整体替换语义：图表把完整警告数组推给
  // setChartWarnings，无需逐条合并。清空时机：run() 开头 + 切换图表类型
  // （切到 wafer 时 UnifiedChartGrid 卸载、无人再 emit，残留靠这里清）。
  // chartType 是字符串，deps 身份稳定。
  const [chartWarnings, setChartWarnings] = useState([]);
  useEffect(() => { setChartWarnings([]); }, [chartType]);

  // Format a single warning into a user-facing string.
  const formatChartWarning = (w) => {
    if (w.kind === 'z_numeric_dropped') {
      const ct = CHART_TYPES.find((c) => c.key === w.chartType)?.label || w.chartType;
      return `当前图表（${ct}）不支持数值 Z（"${w.zField}"）做颜色编码，已忽略 — 请改选类别字段`;
    }
    if (w.kind === 'z_facets_clamped') {
      return `Z 字段 "${w.zField}" 共 ${w.total} 个不同值，分图模式仅显示前 ${w.shown} 个 — 建议改用颜色编码或加筛选条件缩小范围`;
    }
    if (w.kind === 'perf_gl') {
      return `预计绘制 ${w.pointCount || w.rowCount} 个点，已自动切换 WebGL 加速渲染`;
    }
    if (w.kind === 'x_categories_clamped') {
      return `X 字段 "${w.xField}" 共 ${w.total} 个不同值，超出箱型图/小提琴图/蜂群图渲染上限，仅显示前 ${w.shown} 个 — 建议改用散点图或选数值更少的 X`;
    }
    if (w.kind === 'swarm_y_categorical') {
      return `蜂群图暂不支持类别 Y 字段 "${w.yField}" — 请切到箱型图/小提琴图`;
    }
    return JSON.stringify(w);
  };

  const handleSelection = useCallback((e) => {
    if (!e || !Array.isArray(e.points)) return;
    const selectedIds = new Set<string>();
    for (const p of e.points) {
      const row = p.customdata;
      if (Array.isArray(row?.__memberRowIds)) {
        row.__memberRowIds.forEach((id) => selectedIds.add(id));
      } else if (typeof row === 'string') {
        selectedIds.add(row);
      } else if (row?.__rowId) {
        selectedIds.add(row.__rowId);
      }
    }
    if (chartDataset && selectedIds.size > 0) {
      const additive = !!(e.event?.ctrlKey || e.event?.metaKey || e.event?.shiftKey);
      setSelection(chartDataset.id, [...selectedIds], additive ? 'add' : 'replace');
    }
  }, [chartDataset, setSelection]);

  const handlePointClick = useCallback((d) => {
    if (!chartDataset || !d) return;
    const ids = Array.isArray(d.__memberRowIds)
      ? d.__memberRowIds
      : [typeof d === 'string' ? d : d.__rowId].filter(Boolean);
    if (ids.length) setSelection(chartDataset.id, ids, 'replace');
  }, [chartDataset, setSelection]);
  const handleCloseDevice = useCallback(() => setActiveDevice(null), []);

  const openSelectedDevice = useCallback(() => {
    if (!chartDataset || workspaceState.selectedRowIds.size !== 1) return;
    const id = [...workspaceState.selectedRowIds][0];
    const row = chartDataset.rows.find((item) => item.__rowId === id);
    if (row?.id != null) setActiveDevice(row);
  }, [chartDataset, workspaceState.selectedRowIds]);

  const handleWorkspaceKeyDown = useCallback((event) => {
    const target = event.target;
    if (target instanceof Element
      && target.closest('input, textarea, select, option, button, [contenteditable="true"]')) return;
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') {
      event.preventDefault();
      if (event.shiftKey) redo(); else undo();
      return;
    }
    if (event.key === 'Delete' || event.key === 'Backspace') {
      event.preventDefault();
      deleteSelection();
    }
  }, [deleteSelection, redo, undo]);

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
        <button className={`btn${showFilters ? ' active' : ''}`} onClick={() => setShowFilters((v) => !v)}>
          <I.filter size={13} /> 筛选
        </button>
        <button className={`btn${showInspector ? ' active' : ''}`} onClick={() => setShowInspector((v) => !v)}>
          <I.settings size={13} /> 配置
        </button>
        <button className="btn" onClick={undo} disabled={workspaceState.undoStack.length === 0} title="撤销 (Ctrl/Cmd+Z)">
          ↶
        </button>
        <button className="btn" onClick={redo} disabled={workspaceState.redoStack.length === 0} title="重做 (Ctrl/Cmd+Shift+Z)">
          ↷
        </button>
        <button className="btn" onClick={deleteSelection} disabled={workspaceState.selectedRowIds.size === 0} title="删除选中行">
          <I.trash size={13} /> {workspaceState.selectedRowIds.size || ''}
        </button>
        <button className="btn" onClick={() => setFullscreen((v) => !v)} title={fullscreen ? '退出全屏分析' : '全屏分析'}>
          <I.expand size={13} />
        </button>
        <button
          className="btn"
          onClick={onExportCsv}
          disabled={exporting || !chartDataset}
          title="导出当前图表引用的数据表（包含本地编辑和删除）"
        >
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

      <div
        className={`workspace analysis-workspace${fullscreen ? ' fullscreen' : ''}${showFilters ? '' : ' filters-hidden'}${showInspector ? '' : ' inspector-hidden'}`}
        onKeyDown={handleWorkspaceKeyDown}
      >
        {showFilters && <FilterPanel value={filters} onApply={setFilters} />}
        <div className="canvas-wrap">
          {workspaceState.activeTabId === 'chart' ? <div
            className="chart-card"
            tabIndex={0}
            onMouseDown={(event) => event.currentTarget.focus()}
          >
            <div className="chart-head">
              <span className="title">{titleText}</span>
              <span className="axes">
                {stats
                  ? `${rows.length.toLocaleString()} 工作区行 · 查询 ${stats.returned}/${stats.total}${stats.truncated ? ' · 已截断' : ''}${stats.sampled ? ' · 已采样 (' + Math.round(stats.sample_rate * 100) + '%)' : ''}`
                  : '尚未查询'}
              </span>
              <div className="right">
                {workspaceState.datasets.length > 0 && (
                  <select
                    className="select"
                    value={chartDataset?.id || ''}
                    onChange={(event) => setChartDataset(event.target.value)}
                    title="图表数据源"
                  >
                    {workspaceState.datasets.map((dataset) => (
                      <option key={dataset.id} value={dataset.id}>{dataset.name}</option>
                    ))}
                  </select>
                )}
                <button
                  className="btn sm"
                  disabled={workspaceState.selectedRowIds.size !== 1}
                  onClick={openSelectedDevice}
                >
                  查看器件
                </button>
              </div>
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
                  {workspaceState.selectedRowIds.size > 0 && (
                    <div className="explore-selectionbar">
                      已选中 {workspaceState.selectedRowIds.size.toLocaleString()} 行
                      <span>Delete/Backspace 删除 · Ctrl/Cmd+Z 撤销</span>
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
                      rows={rows}
                      xFields={xMeta}
                      yFields={yMeta}
                      zField={zMeta}
                      facetZ={faceting}
                      onWarningsChange={setChartWarnings}
                      onPointClick={handlePointClick}
                      onSelection={handleSelection}
                      selectedRowIds={workspaceState.selectionDatasetId === chartDataset?.id
                        ? workspaceState.selectedRowIds : EMPTY_SELECTION}
                      revision={chartDataset?.id}
                    />
                  )}
                  {isWafer && (
                    <WaferMap
                      rows={rows}
                      valueField={waferZ.name}
                      valueLabel={waferZMeta ? displayLabel(waferZMeta) : waferZ.name}
                      facetField={waferFacet !== NO_FACET ? waferFacet : null}
                      facets={waferFacetValues}
                      aggregated={useAggregate}
                      // 聚合模式下一格已合并多器件，没有 device.id 可跳详情；
                      // 不传 onPointClick 让 WaferMap 内部禁用点击。
                      onPointClick={useAggregate ? undefined : handlePointClick}
                      onSelection={handleSelection}
                      selectedRowIds={workspaceState.selectionDatasetId === chartDataset?.id
                        ? workspaceState.selectedRowIds : EMPTY_SELECTION}
                      revision={chartDataset?.id}
                    />
                  )}
                </div>
              )}
            </div>
          </div> : activeDataset ? (
            <WorksheetGrid
              dataset={activeDataset}
              selectedRowIds={workspaceState.selectionDatasetId === activeDataset.id
                ? workspaceState.selectedRowIds : EMPTY_SELECTION}
              onSelection={(ids, mode) => setSelection(activeDataset.id, ids, mode)}
              onEdit={(rowId, column, value) => editCell(activeDataset.id, rowId, column, value)}
            />
          ) : (
            <div className="analysis-empty-table">请先运行查询创建数据表</div>
          )}
          <WorkspaceTabs
            datasets={workspaceState.datasets}
            activeTabId={workspaceState.activeTabId}
            chartDatasetId={workspaceState.chartDatasetId}
            onActivate={setActiveTab}
            onDuplicate={() => duplicateDataset()}
          />
        </div>
        {showInspector && <Inspector
          fields={fields}
          chartType={chartType}
          xFields={xFields}
          setXFields={setXFields}
          yFields={yFields}
          setYFields={setYFields}
          zField={zField}
          setZField={setZField}
          zMode={zMode}
          setZMode={setZMode}
          zMeta={zMeta}
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
        />}
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
  zMode, setZMode, zMeta,
  waferZ, setWaferZ,
  waferFacet, setWaferFacet,
  xMeta, yMeta, violinXWarning, aggregateXWarning, yzFieldConflict,
  limit, setLimit, stats,
}) {
  const isWafer = chartType === 'wafer';
  const xAxisOptions = useMemo(() => axisFieldOptions(fields, X_AXIS_FIELD_NAMES), [fields]);
  const yAxisOptions = useMemo(() => axisFieldOptions(fields, Y_AXIS_FIELD_NAMES), [fields]);

  // Grid preview text (X cols × Y rows; facet mode adds a Z-block outer loop).
  const zFacetActive = !!zField && zMode === 'facet' && !!zMeta?.isCategorical;
  const gridText = zFacetActive
    ? `按 Z 值分图：每个 Z 值渲染 ${yMeta.length || '?'} 行 × ${xMeta.length || '?'} 列子图，竖向排列`
    : `将渲染 ${yMeta.length || '?'} 行 × ${xMeta.length || '?'} 列 = ${(yMeta.length || 0) * (xMeta.length || 0) || '?'} 个子图`;

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
              <AxisFieldCheckList
                options={xAxisOptions}
                value={xFields}
                onChange={setXFields}
                discouragedSections={['geometric']}
                discouragedHint="几何坐标建议留给 Wafer 版图"
              />
            </div>
            <div className="section">
              <div className="section-title" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span>Y 字段（可多选）</span>
                {xFields.length === 0 && (
                  <span className="hint">请先选择 X 字段</span>
                )}
              </div>
              <AxisFieldCheckListWithAgg
                options={yAxisOptions}
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
              {zField && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                  <span style={{ fontSize: 10.5, color: 'var(--fg-3)' }}>展示方式：</span>
                  {zMeta?.isCategorical ? (
                    Z_MODE_OPTIONS.map((o) => (
                      <label
                        key={o.key}
                        className={`explore-fieldchip${zMode === o.key ? ' checked' : ''} radio`}
                      >
                        <input
                          type="radio"
                          checked={zMode === o.key}
                          onChange={() => setZMode(o.key)}
                          style={{ position: 'absolute', opacity: 0, pointerEvents: 'none' }}
                        />
                        <span className="explore-fieldchip-radio" aria-hidden>
                          {zMode === o.key && <span className="dot" />}
                        </span>
                        <span className="explore-fieldchip-label">{o.label}</span>
                      </label>
                    ))
                  ) : (
                    <span className="hint" style={{ fontSize: 10.5 }}>数值 Z 仅支持颜色编码</span>
                  )}
                </div>
              )}
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
 * AxisFieldCheckList — compact multi-select list for the curated X axis fields.
 * ----------------------------------------------------------------------- */
function AxisFieldCheckList({
  options,
  value,
  onChange,
  discouragedSections = [],
  discouragedHint = '',
}: {
  options: any[];
  value: string[];
  onChange: (v: string[]) => void;
  discouragedSections?: string[];
  discouragedHint?: string;
}) {
  const selected = useMemo(() => new Set(value), [value]);
  const toggle = (name: string) => {
    if (selected.has(name)) onChange(value.filter((v) => v !== name));
    else onChange([...value, name]);
  };
  const hasDiscouragedSelected = useMemo(
    () => Array.from(selected).some((name) => {
      const f = options?.find((o) => o.name === name);
      return f && discouragedSections.includes(f.section);
    }),
    [selected, options, discouragedSections]
  );
  if (!options) return <div className="dim" style={{ fontSize: 11 }}>加载中…</div>;
  if (options.length === 0) {
    return <div className="dim" style={{ fontSize: 11 }}>暂无可用 X 字段</div>;
  }
  return (
    <div className="explore-fieldlist compact">
      {discouragedHint && hasDiscouragedSelected && (
        <div className="hint" style={{ marginBottom: 6, fontSize: 11 }}>{discouragedHint}</div>
      )}
      <div className="explore-fieldgroup">
        <div className="explore-fieldgroup-body">
          {options.map((f) => {
            const checked = selected.has(f.name);
            const discouraged = discouragedSections.includes(f.section);
            return (
              <label
                key={f.name}
                className={`explore-fieldchip${checked ? ' checked' : ''}${discouraged ? ' discouraged' : ''}`}
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
                <span className="explore-fieldchip-label" style={discouraged ? { opacity: 0.6 } : undefined}>{axisPickerLabel(f)}</span>
              </label>
            );
          })}
        </div>
      </div>
    </div>
  );
}


/* -------------------------------------------------------------------------
 * AxisFieldCheckListWithAgg — compact multi-select list for curated Y fields.
 * ----------------------------------------------------------------------- */
function AxisFieldCheckListWithAgg({ options, value, onChange, disabled = false }) {
  const byName = useMemo(() => {
    const m = new Map();
    for (const v of value) m.set(v.name, v);
    return m;
  }, [value]);
  if (!options) return <div className="dim" style={{ fontSize: 11 }}>加载中…</div>;
  if (options.length === 0) {
    return <div className="dim" style={{ fontSize: 11 }}>暂无可用 Y 字段</div>;
  }
  const toggle = (name) => {
    if (disabled) return;
    if (byName.has(name)) onChange(value.filter((v) => v.name !== name));
    else onChange([...value, { name, aggregation: 'all' }]);
  };
  const setAgg = (name, agg) => {
    onChange(value.map((v) => (v.name === name ? { ...v, aggregation: agg } : v)));
  };
  return (
    <div className="explore-fieldlist compact">
      <div className="explore-fieldgroup">
        <div className="explore-fieldgroup-body">
          {options.map((f) => {
            const entry = byName.get(f.name);
            const checked = !!entry;
            const isCategorical = CATEGORICAL_SECTIONS.has(f.section);
            return (
              <span
                key={f.name}
                style={{ display: 'inline-flex', alignItems: 'center', gap: 3, opacity: disabled ? 0.6 : 1 }}
              >
                <label
                  className={`explore-fieldchip${checked ? ' checked' : ''}`}
                  title={displayLabel(f)}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={disabled}
                    onChange={() => toggle(f.name)}
                    style={{ position: 'absolute', opacity: 0, pointerEvents: 'none' }}
                  />
                  <span className="explore-fieldchip-cb" aria-hidden>
                    {checked && <I.check size={9} stroke="#fff" sw={2.5} />}
                  </span>
                  <span className="explore-fieldchip-label">{axisPickerLabel(f)}</span>
                </label>
                {checked && !isCategorical && (
                  <select
                    className="input mono"
                    value={entry.aggregation || 'all'}
                    onChange={(e) => setAgg(f.name, e.target.value)}
                    disabled={disabled}
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
