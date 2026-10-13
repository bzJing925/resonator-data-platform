import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Plotly from 'plotly.js/dist/plotly';
import createPlotlyComponent from 'react-plotly.js/factory';
import { buildCachedJmpChartScene } from '../features/analysis/chart/chartSceneCache';
import {
  buildSelectionRestyle,
  buildYAxisAutorangeUpdate,
  buildYDataRanges,
  buildYAxisLayoutRanges,
  datumRowIds,
  type YAxisRanges,
} from '../features/analysis/chart/chartInteraction';
import { buildPointStyleRestyles } from '../features/analysis/pointStyle';
import { formatMedianLabel, medianOf } from '../features/analysis/chart/buildSwarmMedianTraces';
import type { PointStyleOverride } from '../features/analysis/model/commands';
import type { ChartField } from '../types';

const Plot = createPlotlyComponent(Plotly);
const getPlotly = () => Promise.resolve(Plotly);

function reportPlotlyError(operation: string, error: unknown) {
  console.error(`[AnalysisChart] Plotly ${operation} failed`, error);
}

function applyPlotSelection(graphDiv: any, selectedRowIds?: Set<string>) {
  if (!graphDiv?.data) return;
  const { traceIndices, selectedpoints } = buildSelectionRestyle(graphDiv.data, selectedRowIds);
  if (traceIndices.length === 0) return;
  const hasSelection = !!selectedRowIds?.size;
  const hasAppliedSelection = traceIndices.some((traceIndex) =>
    graphDiv.data[traceIndex]?.selectedpoints != null);
  if (!hasSelection && !hasAppliedSelection) return;
  getPlotly().then((Plotly) => {
    try {
      Promise.resolve(Plotly.restyle(graphDiv, {
        selectedpoints,
        'selected.marker.opacity': 1,
        'unselected.marker.opacity': hasSelection ? 0.18 : 1,
      }, traceIndices))
        .catch((error) => reportPlotlyError('restyle selection', error));
    } catch (error) {
      reportPlotlyError('restyle selection', error);
    }
  }).catch((error) => reportPlotlyError('load for selection', error));
}

function applyYAxisAutorange(graphDiv: any) {
  if (!graphDiv?.layout) return;
  const update = buildYAxisAutorangeUpdate({ ...graphDiv.layout,
    meta: { ...graphDiv.layout.meta, analysisYRanges: graphDiv.layout.meta?.analysisActiveYRanges
      || graphDiv.layout.meta?.analysisYRanges } }, graphDiv.data);
  if (Object.keys(update).length === 0) return;
  getPlotly().then((Plotly) => {
    try {
      Promise.resolve(Plotly.relayout(graphDiv, update))
        .catch((error) => reportPlotlyError('relayout Y autorange', error));
    } catch (error) {
      reportPlotlyError('relayout Y autorange', error);
    }
  }).catch((error) => reportPlotlyError('load for Y autorange', error));
}

function applyAxesAutorange(graphDiv: any, numericY = false) {
  if (!graphDiv?.layout) return;
  const update = Object.keys(graphDiv.layout).reduce<Record<string, unknown>>((axes, key) => {
    if ((numericY ? /^xaxis\d*$/ : /^[xy]axis\d*$/).test(key)) axes[`${key}.autorange`] = true;
    return axes;
  }, {});
  if (numericY) Object.assign(update, buildYAxisAutorangeUpdate(graphDiv.layout, graphDiv.data));
  if (Object.keys(update).length === 0) return;
  getPlotly().then((Plotly) => {
    Promise.resolve(Plotly.relayout(graphDiv, update))
      .catch((error) => reportPlotlyError('relayout axes autorange', error));
  }).catch((error) => reportPlotlyError('load for axes autorange', error));
}

function clearPlotSelectionOutline(graphDiv: any) {
  if (!graphDiv?.layout?.selections?.length) return;
  Promise.resolve(Plotly.relayout(graphDiv, { selections: [] }))
    .catch((error) => reportPlotlyError('clear selection outline', error));
}

function nearestRawDatum(graph: any, clientX: number, clientY: number) {
  const rect = graph.getBoundingClientRect();
  const localX = (clientX - rect.left) * graph._fullLayout.width / rect.width;
  const localY = (clientY - rect.top) * graph._fullLayout.height / rect.height;
  let nearest: { datum: unknown; distance: number } | null = null;
  for (const trace of graph.data) {
    if (!Array.isArray(trace.customdata)
      || (trace.meta?.analysisLayer && trace.meta.analysisLayer !== 'raw-points')) continue;
    const xaxis = graph._fullLayout[(trace.xaxis || 'x').replace('x', 'xaxis')];
    const yaxis = graph._fullLayout[(trace.yaxis || 'y').replace('y', 'yaxis')];
    if (!xaxis?.d2p || !yaxis?.d2p
      || localX < xaxis._offset || localX > xaxis._offset + xaxis._length
      || localY < yaxis._offset || localY > yaxis._offset + yaxis._length) continue;
    for (let index = 0; index < trace.customdata.length; index += 1) {
      const x = xaxis._offset + xaxis.d2p(trace.x[index]);
      const y = yaxis._offset + yaxis.d2p(trace.y[index]);
      const size = Array.isArray(trace.marker?.size) ? trace.marker.size[index] : trace.marker?.size;
      const distance = Math.hypot(x - localX, y - localY);
      if (distance > Math.max(8, (size || 6) / 2 + 3) || !Number.isFinite(distance)) continue;
      const datum = trace.customdata[index];
      if (datumRowIds(datum).length && (!nearest || distance < nearest.distance)) {
        nearest = { datum, distance };
      }
    }
  }
  return nearest?.datum;
}

function applyPlotPointStyles(
  graphDiv: any,
  sourceTraces: any[],
  pointStyles?: Record<string, PointStyleOverride>,
) {
  if (!graphDiv?.data) return;
  const restyles = buildPointStyleRestyles(sourceTraces, pointStyles);
  if (restyles.length === 0) return;
  getPlotly().then((Plotly) => {
    try {
      restyles.reduce<Promise<unknown>>((pending, { traceIndex, update }) => pending.then(() =>
        Plotly.restyle(graphDiv, update, [traceIndex])), Promise.resolve())
        .catch((error) => reportPlotlyError('restyle point styles', error));
    } catch (error) {
      reportPlotlyError('restyle point styles', error);
    }
  }).catch((error) => reportPlotlyError('load for point styles', error));
}

// Plotly's bundled TypeScript definitions are very strict about literal
// unions (axis type, legend orientation, grid roworder, etc.). Our chart
// code builds layouts dynamically, so we cast through this wrapper to
// avoid fighting the type checker on every trace/layout property.
const PlotAny = Plot as unknown as React.FC<any>;

// Sentinel for null/undefined Z bucket — kept as a literal so the legend
// reads "∅" and groupBy() / distinctSortedValues() / per-trace filters all
// agree on the same key. Without this, rows with null Z fall out of
// every per-Z trace and silently disappear from the chart.
const NULL_KEY = '∅';
const isNullish = (v) => v === null || v === undefined;

const baseLayout: any = {
  paper_bgcolor: '#181B21',
  plot_bgcolor: '#181B21',
  font: { family: 'Inter, system-ui, sans-serif', size: 11, color: '#C4C8D0' },
  margin: { l: 56, r: 16, t: 24, b: 72 },
  xaxis: {
    gridcolor: 'rgba(95,168,211,0.10)',
    zeroline: false,
    automargin: true,
    tickangle: -35,
    nticks: 12,
    tickfont: { color: '#8A919D', size: 10 },
  },
  yaxis: {
    gridcolor: 'rgba(95,168,211,0.10)',
    zeroline: false,
    automargin: true,
    nticks: 10,
    tickfont: { color: '#8A919D', size: 10 },
  },
  hoverlabel: { bgcolor: '#0F1115', bordercolor: '#2A2E36', font: { color: '#E7E9ED', family: 'JetBrains Mono, monospace' } },
};

const baseConfig = {
  displaylogo: false,
  responsive: true,
  // select2d / lasso2d 启用 — Explore.jsx 监听 onSelected 实现"框选隐藏点"
  modeBarButtonsToRemove: [],
};

const PALETTE = ['#5FA8D3', '#4BD097', '#E8C547', '#9B7EDC', '#E86B5C', '#4ECBD9', '#D97AB3', '#A3D94E',
                 '#7BBCE3', '#B8E08A', '#E8A85C', '#C99AE6'];

// Colorscale used when colorKey is numeric.
const NUMERIC_COLORSCALE = 'Viridis';

/* ------------------------------------------------------------------
 *  Helpers
 * ------------------------------------------------------------------ */

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

// Group rows by a categorical (or any) key; returns Map<key, rows[]>.
function groupBy(rows, key) {
  if (!rows || rows.length === 0) return new Map();
  const m = new Map();
  rows.forEach((r) => {
    const k = r?.[key];
    const sk = k === null || k === undefined ? '∅' : k;
    if (!m.has(sk)) m.set(sk, []);
    m.get(sk).push(r);
  });
  return m;
}

// Match strings that are exactly a finite number (no trailing letters).
const PURE_NUMBER_RE = /^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/;

// Sort categorical keys (numbers numerically, strings lexically).
// `parseFloat('1A') === 1` is *not* a number for our purposes — the
// trailing 'A' carries meaning and would be dropped by numeric sort.
function sortKeys(keys) {
  const arr = [...keys];
  const allNum = arr.every((k) =>
    typeof k === 'number' || (typeof k === 'string' && PURE_NUMBER_RE.test(k)),
  );
  if (allNum) return arr.sort((a, b) => parseFloat(a) - parseFloat(b));
  // localeCompare with numeric:true gives natural sort for mixed strings
  // (e.g. wafer-1, wafer-2, wafer-10 instead of 1, 10, 2).
  return arr.sort((a, b) => String(a).localeCompare(String(b), undefined, { numeric: true }));
}

/* ------------------------------------------------------------------
 *  LineChart — basic single/multi-series line.
 * ------------------------------------------------------------------ */
export function LineChart({ x, y, xLabel, yLabel, name, color, markers = [], series, showLegend, xIsCategory, extraShapes = [], onSeriesClick, onLegendSelect }: {
  x?: number[];
  y?: number[];
  xLabel?: string;
  yLabel?: string;
  name?: string;
  color?: string;
  markers?: { x: number; label?: string; color?: string }[];
  series?: { x?: number[]; y: number[]; name?: string; color?: string; width?: number; opacity?: number; visible?: boolean | 'legendonly'; mode?: string; dash?: string; customdata?: string[] }[];
  showLegend?: boolean;
  xIsCategory?: boolean;
  extraShapes?: unknown[];
  onSeriesClick?: (key: string) => void;
  onLegendSelect?: (key: string) => void;
}) {
  const traces = useMemo(() => {
    if (series && series.length) {
      return series.map((s, i) => ({
        type: 'scattergl',
        mode: s.mode || 'lines+markers',
        x: s.x || x,
        y: s.y,
        name: s.name || `曲线 ${i + 1}`,
        connectgaps: false,
        line: {
          color: s.color || PALETTE[i % PALETTE.length],
          width: s.width != null ? s.width : 1.4,
          dash: s.dash || 'solid',
        },
        marker: {
          color: s.color || PALETTE[i % PALETTE.length],
          size: onSeriesClick || onLegendSelect ? 8 : 5,
          opacity: 0.85,
        },
        opacity: s.opacity != null ? s.opacity : 1,
        visible: s.visible != null ? s.visible : true,
        customdata: s.customdata,
        // 把曲线 key 放到 trace meta，图例点击时反查
        meta: s.customdata && s.customdata.length ? { key: s.customdata[0] } : undefined,
      }));
    }
    return [{
      type: 'scattergl',
      mode: 'lines',
      x,
      y,
      name: name || 'trace',
      line: { color: color || PALETTE[0], width: 1.4 },
    }];
  }, [series, x, y, name, color, onSeriesClick, onLegendSelect]);

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
      hoverdistance: 30,
      xaxis: { ...baseLayout.xaxis, title: xLabel || 'x', type: xIsCategory ? 'category' : undefined },
      yaxis: { ...baseLayout.yaxis, title: yLabel || 'y' },
      shapes: [...markerShapes, ...extraShapes],
      annotations,
    };
  }, [xLabel, yLabel, showLegend, xIsCategory, markers, extraShapes]);

  const handleClick = useCallback((e) => {
    if (!onSeriesClick || !e.points || !e.points[0]) return;
    const key = e.points[0].customdata;
    if (typeof key === 'string') onSeriesClick(key);
  }, [onSeriesClick]);

  const handleLegendClick = useCallback((e) => {
    if (!onLegendSelect) return;
    // 优先从 customdata 第一格取曲线 key；meta 作为兜底
    const key = e.data[e.curveNumber]?.customdata?.[0] ?? e.data[e.curveNumber]?.meta?.key;
    if (typeof key === 'string') onLegendSelect(key);
    // 返回 false 阻止 Plotly 默认的“点击图例隐藏曲线”行为
    return false;
  }, [onLegendSelect]);

  const handleLegendDoubleClick = useCallback((e: any) => {
    // 双击图例也当作选中/高亮，和单击保持一致
    if (onLegendSelect) {
      const key = e.data[e.curveNumber]?.customdata?.[0] ?? e.data[e.curveNumber]?.meta?.key;
      if (typeof key === 'string') onLegendSelect(key);
    }
    // 禁用默认的“双击图例只保留一条”行为
    return false;
  }, [onLegendSelect]);

  return (
    <PlotAny
      divId="chart-line"
      data={traces}
      layout={layout}
      config={baseConfig}
      style={{ width: '100%', height: '100%' }}
      useResizeHandler
      onClick={handleClick}
      onLegendClick={handleLegendClick}
      onLegendDoubleClick={handleLegendDoubleClick}
    />
  );
}

/* ------------------------------------------------------------------
 *  WaferMap
 *
 *  Render every device at its physical (x, y) die coordinate as a
 *  square marker, color-encoded by `valueField`. If `facetField` is
 *  set, build a 1-row × N-col subplot grid (one panel per facet
 *  value, sharing the colorbar). 1:1 aspect ratio so dies look square.
 *
 *  When `aggregated` is false, multiple devices may share an (x, y)
 *  cell; only the last one is visible. The tooltip then lists how
 *  many devices share the cell so the user is aware. To show a
 *  unique value per cell, the caller should aggregate upstream and
 *  pass aggregated=true.
 * ------------------------------------------------------------------ */
export function buildWaferSpec({ rows, valueField, valueLabel, facetField, facets, aggregated = false, colorDomain = undefined as [number, number] | null | undefined }) {
    const allXs = rows.map((r) => r['x']).filter(Number.isFinite);
    const allYs = rows.map((r) => r['y']).filter(Number.isFinite);
    const xMin = allXs.length ? allXs.reduce((a, b) => Math.min(a, b), Infinity) - 1 : -1;
    const xMax = allXs.length ? allXs.reduce((a, b) => Math.max(a, b), -Infinity) + 1 : 1;
    const yMin = allYs.length ? allYs.reduce((a, b) => Math.min(a, b), Infinity) - 1 : -1;
    const yMax = allYs.length ? allYs.reduce((a, b) => Math.max(a, b), -Infinity) + 1 : 1;

    const allVals = rows.map((r) => r[valueField]).filter(Number.isFinite);
    const vMin = colorDomain?.[0] ?? (allVals.length ? allVals.reduce((a, b) => Math.min(a, b), Infinity) : 0);
    const vMax = colorDomain?.[1] ?? (allVals.length ? allVals.reduce((a, b) => Math.max(a, b), -Infinity) : 1);

    const useFacets = !!facetField && facets && facets.length > 0;
    const panels = useFacets
      ? facets.map((fv) => ({ key: String(fv), rows: rows.filter((r) => String(r[facetField]) === String(fv)) }))
      : [{ key: 'all', rows }];

    const markerSize = Math.max(8, Math.round(22 / Math.sqrt(panels.length)));

    const outTraces = [];
    const outLayout = {
      ...baseLayout,
      showlegend: false,
      margin: { l: 56, r: 88, t: 36, b: 44 },
      annotations: [],
    };

    if (useFacets) {
      outLayout.grid = { rows: 1, columns: panels.length, pattern: 'independent' };
    }

    panels.forEach((panel, pIdx) => {
      const axisIdx = pIdx + 1;
      const xref = `x${axisIdx === 1 ? '' : axisIdx}`;
      const yref = `y${axisIdx === 1 ? '' : axisIdx}`;
      const xKeyAxis = `xaxis${axisIdx === 1 ? '' : axisIdx}`;
      const yKeyAxis = `yaxis${axisIdx === 1 ? '' : axisIdx}`;

      outLayout[xKeyAxis] = {
        ...baseLayout.xaxis,
        title: 'X（芯片坐标）',
        range: [xMin, xMax],
        dtick: 1,
        zeroline: false,
      };
      outLayout[yKeyAxis] = {
        ...baseLayout.yaxis,
        title: pIdx === 0 ? 'Y（芯片坐标）' : undefined,
        range: [yMin, yMax],
        dtick: 1,
        scaleanchor: xref,
        scaleratio: 1,
        zeroline: false,
      };

      if (useFacets) {
        const xDomainCenter = (pIdx + 0.5) / panels.length;
        outLayout.annotations.push({
          x: xDomainCenter, y: 1.02, xref: 'paper', yref: 'paper',
          text: `${facetField} = ${panel.key}`, showarrow: false,
          font: { size: 11, color: '#475569' },
        });
      }

      const pRows = panel.rows;
      let tooltipRows = pRows;
      if (!aggregated) {
        const cellCount = new Map();
        for (const r of pRows) {
          const key = `${r.x}|${r.y}`;
          cellCount.set(key, (cellCount.get(key) || 0) + 1);
        }
        tooltipRows = pRows.map((r) => ({ ...r, _cell_n: cellCount.get(`${r.x}|${r.y}`) || 1 }));
      }

      const hoverTemplate = aggregated
        ? '<b>x=%{x}, y=%{y}</b><br>'
          + (valueLabel || valueField) + ': %{marker.color:.4g}'
          + '<br>此格器件数: %{customdata._cell_n}<br>有效数: %{customdata._cell_valid_n}<extra></extra>'
        : '<b>编号 %{customdata.id}</b><br>'
          + 'x=%{x}, y=%{y}<br>'
          + (valueLabel || valueField) + ': %{marker.color:.4g}<br>'
          + '此格器件数: %{customdata._cell_n}'
          + '<extra></extra>';

      outTraces.push({
        type: 'scattergl',
        mode: 'markers',
        x: pRows.map((r) => r.x),
        y: pRows.map((r) => r.y),
        xaxis: xref,
        yaxis: yref,
        marker: {
          size: markerSize,
          symbol: 'square',
          color: pRows.map((r) => r[valueField]),
          colorscale: NUMERIC_COLORSCALE,
          cmin: vMin,
          cmax: vMax,
          showscale: colorDomain !== null && pIdx === panels.length - 1,
          colorbar: pIdx === panels.length - 1
            ? { title: { text: valueLabel || valueField, side: 'right' }, thickness: 12, len: 0.7, x: 1.02 }
            : undefined,
          line: { width: 1, color: 'rgba(15,23,42,0.25)' },
          opacity: 0.9,
        },
        customdata: tooltipRows,
        hovertemplate: hoverTemplate,
        name: panel.key,
      });
    });

    return { traces: outTraces, layout: outLayout };
}

export function WaferMap({
  plotId = 'chart-wafer', colorDomain = undefined as [number, number] | null | undefined,
  rows, valueField, valueLabel,
  facetField, facets,
  aggregated = false,
  onPointClick,
  onSelection,
  selectedRowIds,
  revision,
  interactionMode = 'select',
  autorangeRequest = 0,
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const graphDivRef = useRef<any>(null);
  const { traces, layout } = useMemo(() => buildWaferSpec({ rows, valueField, valueLabel, facetField, facets, aggregated, colorDomain }), [rows, valueField, valueLabel, facetField, facets, aggregated, colorDomain]);
  const plotLayout = useMemo(
    () => ({
      ...layout,
      dragmode: interactionMode,
      uirevision: revision || 'wafer-analysis',
    }),
    [layout, revision, interactionMode],
  );
  const plotConfig = useMemo(() => ({
    ...baseConfig,
    displayModeBar: false,
    scrollZoom: true,
    doubleClick: 'reset+autosize',
  }), []);

  const handleClick = useCallback((e) => {
    if (onPointClick && e.points && e.points[0]) {
      onPointClick(e.points[0].customdata);
    }
  }, [onPointClick]);

  useEffect(() => {
    applyPlotSelection(graphDivRef.current, selectedRowIds);
  }, [selectedRowIds, traces]);

  useEffect(() => {
    if (autorangeRequest > 0) applyAxesAutorange(graphDivRef.current);
  }, [autorangeRequest]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || typeof ResizeObserver === 'undefined') return;
    let frame = 0;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        if (graphDivRef.current) {
          getPlotly().then((Plotly) => Plotly.Plots.resize(graphDivRef.current));
        }
      });
    });
    observer.observe(container);
    return () => { cancelAnimationFrame(frame); observer.disconnect(); };
  }, []);

  return (
    <div ref={containerRef} style={{ width: '100%', height: '100%' }}>
    <PlotAny
      divId={plotId}
      data={traces}
      layout={plotLayout}
      config={plotConfig}
      style={{ width: '100%', height: '100%' }}
      useResizeHandler
      onInitialized={(_figure, graphDiv) => {
        graphDivRef.current = graphDiv;
        applyPlotSelection(graphDiv, selectedRowIds);
      }}
      onUpdate={(_figure, graphDiv) => { graphDivRef.current = graphDiv; }}
      onClick={handleClick}
      onSelected={onSelection}
    />
    </div>
  );
}

/* ------------------------------------------------------------------
 *  UnifiedChartGrid
 *
 *  Unified renderer that lays out a (yFields × xFields) subplot grid
 *  for chartType ∈ {scatter, box, violin, line}. Each cell renders a
 *  chart of the chosen type for that (xField, yField) pair, optionally
 *  colored / split by `zField`.
 *
 *    - xFields / yFields: array of { name, label, unit, isCategorical }
 *    - zField: { name, label, unit, isCategorical } | null
 *    - height: per-cell height (px). Default 350.
 *
 *  Empty xFields or yFields → placeholder.
 * ------------------------------------------------------------------ */

// 全量行中某数值字段的并集量程（带 3% 边距）；无有限值时返回 null。
// 分面时给同列各块显式共享量程，跨块可比。
function numericRange(rows, key) {
  let lo = Infinity, hi = -Infinity;
  for (const r of rows) {
    const v = r?.[key];
    if (isNum(v)) {
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
  }
  if (!(lo <= hi)) return null;
  if (lo === hi) { lo -= 1; hi += 1; }
  const pad = (hi - lo) * 0.03;
  return [lo - pad, hi + pad];
}

// Resolve all distinct values of a categorical field across rows, sorted.
// When `includeNull` is true, a NULL_KEY sentinel is appended so trace
// builders can render an "unknown" bucket instead of dropping null rows.
function distinctSortedValues(rows, key, { includeNull = false } = {}) {
  const set = new Set();
  let hasNull = false;
  rows.forEach((r) => {
    const v = r?.[key];
    if (isNullish(v)) hasNull = true;
    else set.add(v);
  });
  const sorted = sortKeys(set);
  if (includeNull && hasNull) sorted.push(NULL_KEY);
  return sorted;
}

// Build the "rows belonging to category zv" predicate. When zv is the
// NULL_KEY sentinel, match rows whose value is null/undefined.
function rowsForZ(rows, zKey, zv) {
  if (zv === NULL_KEY) return rows.filter((r) => isNullish(r[zKey]));
  return rows.filter((r) => r[zKey] === zv);
}

// Sub-axis suffix: cell index 1 → '', cell index 2 → '2', ...
function axisSuffix(idx) {
  return idx === 1 ? '' : String(idx);
}

// Format axis title from field meta.
function axisTitle(field) {
  if (!field) return '';
  const lbl = field.label || field.name;
  return field.unit ? `${lbl} (${field.unit})` : lbl;
}

/* ----- per-cell trace builders ----- */

// Scatter / strip plot for one (xField, yField, zField) cell.
function buildScatterTraces({ rows, xField, yField, zField, axisRef, useGl, zCategoryValues, zGroups, showLegend, xCategoryArray }) {
  const xKey = xField.name;
  const yKey = yField.name;
  const xref = `x${axisRef}`;
  const yref = `y${axisRef}`;
  const xIsCat = !!xField.isCategorical;
  const yIsCat = !!yField.isCategorical;
  // Map categorical X to numeric tick positions in GL mode. Plotly's native
  // categorical scatter falls back to SVG and becomes the dominant pan cost.
  const wantGl = useGl && !yIsCat;
  const traces = [];

  // When the X (or Y) axis is rendered as a category axis, stringify
  // the trace coordinates so they match the layout's string
  // categoryarray. Numeric axes keep their raw numeric values.
  const xIndex = new Map((xCategoryArray || []).map((value, index) => [String(value), index]));
  const mapX = xIsCat
    ? (wantGl ? (d) => xIndex.get(String(d[xKey])) : (d) => String(d[xKey]))
    : (d) => d[xKey];
  const mapY = yIsCat ? (d) => String(d[yKey]) : (d) => d[yKey];

  if (zField && !zField.isCategorical) {
    // Numeric Z → single trace, marker.color encodes Z. Show only one
    // shared colorbar (the caller decides which cell does so).
    traces.push({
      type: wantGl ? 'scattergl' : 'scatter',
      mode: 'markers',
      x: rows.map(mapX),
      y: rows.map(mapY),
      xaxis: xref,
      yaxis: yref,
      marker: {
        size: 6,
        color: rows.map((d) => d[zField.name]),
        colorscale: NUMERIC_COLORSCALE,
        showscale: showLegend,
        colorbar: showLegend
          ? { title: { text: axisTitle(zField), side: 'right' }, thickness: 12, len: 0.6, x: 1.02 }
          : undefined,
        opacity: 0.85,
      },
      showlegend: false,
      customdata: rows,
      hovertemplate: `<b>%{x}</b>, %{y}<br>${axisTitle(zField)}: %{marker.color}<extra></extra>`,
    });
  } else if (zField && zField.isCategorical) {
    // Categorical Z → one trace per Z value. Use legendgroup to share
    // a single legend across all cells; only the FIRST cell shows the
    // legend entry.
    const zKey = zField.name;
    const zVals = zCategoryValues || distinctSortedValues(rows, zKey, { includeNull: true });
    let i = 0;
    for (const zv of zVals) {
      const grp = zGroups?.get(zv) || rowsForZ(rows, zKey, zv);
      traces.push({
        type: wantGl ? 'scattergl' : 'scatter',
        mode: 'markers',
        x: grp.map(mapX),
        y: grp.map(mapY),
        xaxis: xref,
        yaxis: yref,
        name: String(zv),
        legendgroup: String(zv),
        showlegend: showLegend,
        marker: {
          size: (xIsCat || yIsCat) ? 6 : 5,
          color: PALETTE[i % PALETTE.length],
          opacity: 0.78,
          line: (xIsCat || yIsCat) ? { width: 0.5, color: '#ffffff80' } : undefined,
        },
        customdata: grp,
        hovertemplate: `<b>%{x}</b>, %{y}<extra>${zv}</extra>`,
      });
      i++;
    }
  } else {
    // Single trace, single color.
    traces.push({
      type: wantGl ? 'scattergl' : 'scatter',
      mode: 'markers',
      x: rows.map(mapX),
      y: rows.map(mapY),
      xaxis: xref,
      yaxis: yref,
      name: 'all',
      showlegend: false,
      marker: { size: (xIsCat || yIsCat) ? 6 : 5, color: PALETTE[0], opacity: 0.78 },
      customdata: rows,
      hovertemplate: `<b>%{x}</b>, %{y}<extra></extra>`,
    });
  }
  return traces;
}

// Box plot for one cell.
//
// Standard (Y numeric): vertical boxes, one per X category (or per Z if zField).
// Y categorical:        horizontal boxes (orientation 'h'), one per Y category;
//                       distribution along X (numeric). If zField categorical,
//                       split each Y row by Z (boxmode 'group').
//
// `cellGroup` is a unique-per-cell string used as `offsetgroup` so that
// plotly's boxmode='group' allocates side-by-side slots PER CELL — not
// across cells. Without this, two cells in the same row whose traces
// happen to share a name (e.g. both named after Y) end up sharing a
// global slot table and shift their boxes off-tick.
function buildBoxTraces({ rows, xField, yField, zField, axisRef, zCategoryValues, zGroups, showLegend, cellGroup }) {
  const xKey = xField.name;
  const yKey = yField.name;
  const xref = `x${axisRef}`;
  const yref = `y${axisRef}`;
  const xIsCat = !!xField.isCategorical;
  const yIsCat = !!yField.isCategorical;
  const traces = [];

  if (yIsCat) {
    // Horizontal box: y is the category, x is the numeric value.
    // If the X axis is *also* categorical (rare but possible), stringify so
    // box positions match the layout's string categoryarray.
    const mapX = xIsCat ? (d) => String(d[xKey]) : (d) => d[xKey];
    if (zField && zField.isCategorical) {
      const zKey = zField.name;
      const zVals = zCategoryValues || distinctSortedValues(rows, zKey, { includeNull: true });
      let i = 0;
      for (const zv of zVals) {
        const grp = zGroups?.get(zv) || rowsForZ(rows, zKey, zv);
        traces.push({
          type: 'box',
          orientation: 'h',
          x: grp.map(mapX),
          y: grp.map((d) => String(d[yKey])),
          xaxis: xref,
          yaxis: yref,
          name: String(zv),
          legendgroup: String(zv),
          offsetgroup: `${cellGroup}::${zv}`,
          alignmentgroup: cellGroup,
          showlegend: showLegend,
          marker: { color: PALETTE[i % PALETTE.length] },
          boxpoints: 'outliers',
        });
        i++;
      }
    } else {
      // No Z grouping: one trace covering all Y categories. (One trace
      // per Y combined with boxmode='group' would offset each box off
      // its tick.)
      traces.push({
        type: 'box',
        orientation: 'h',
        x: rows.map(mapX),
        y: rows.map((d) => String(d[yKey])),
        xaxis: xref,
        yaxis: yref,
        name: axisTitle(xField),
        offsetgroup: cellGroup,
        alignmentgroup: cellGroup,
        showlegend: false,
        marker: { color: PALETTE[0] },
        boxpoints: 'outliers',
        customdata: rows,
      });
    }
    return traces;
  }

  if (zField && zField.isCategorical) {
    const zKey = zField.name;
    const zVals = zCategoryValues || distinctSortedValues(rows, zKey, { includeNull: true });
    let i = 0;
    for (const zv of zVals) {
      const grp = zGroups?.get(zv) || rowsForZ(rows, zKey, zv);
      traces.push({
        type: 'box',
        x: grp.map((d) => String(d[xKey])),
        y: grp.map((d) => d[yKey]),
        xaxis: xref,
        yaxis: yref,
        name: String(zv),
        legendgroup: String(zv),
        offsetgroup: `${cellGroup}::${zv}`,
        alignmentgroup: cellGroup,
        showlegend: showLegend,
        marker: { color: PALETTE[i % PALETTE.length] },
        boxpoints: 'outliers',
        customdata: grp,
      });
      i++;
    }
  } else {
    // No Z grouping: emit a SINGLE trace with all rows. One-trace-per-X
    // combined with boxmode='group' would offset each box into its own
    // sub-slot inside its category, leaving the box visually shifted
    // away from the X tick. A single trace puts each box centered on
    // its X category.
    traces.push({
      type: 'box',
      x: rows.map((d) => String(d[xKey])),
      y: rows.map((d) => d[yKey]),
      xaxis: xref,
      yaxis: yref,
      name: axisTitle(yField),
      offsetgroup: cellGroup,
      alignmentgroup: cellGroup,
      showlegend: false,
      marker: { color: PALETTE[0] },
      boxpoints: 'outliers',
      customdata: rows,
    });
  }
  return traces;
}

// Violin plot for one cell. Y categorical → horizontal violin (one per Y).
//
// `cellGroup` is a unique-per-cell offsetgroup; see buildBoxTraces for why.
function buildViolinTraces({ rows, xField, yField, zField, axisRef, zCategoryValues, zGroups, showLegend, cellGroup }) {
  const xKey = xField.name;
  const yKey = yField.name;
  const xref = `x${axisRef}`;
  const yref = `y${axisRef}`;
  const xIsCat = !!xField.isCategorical;
  const yIsCat = !!yField.isCategorical;
  const traces = [];

  if (yIsCat) {
    // Stringify X when the X axis is categorical so trace points align
    // with the layout's string categoryarray.
    const mapX = xIsCat ? (d) => String(d[xKey]) : (d) => d[xKey];
    if (zField && zField.isCategorical) {
      const zKey = zField.name;
      const zVals = zCategoryValues || distinctSortedValues(rows, zKey, { includeNull: true });
      let i = 0;
      for (const zv of zVals) {
        const grp = zGroups?.get(zv) || rowsForZ(rows, zKey, zv);
        const c = PALETTE[i % PALETTE.length];
        traces.push({
          type: 'violin',
          orientation: 'h',
          x: grp.map(mapX),
          y: grp.map((d) => String(d[yKey])),
          xaxis: xref,
          yaxis: yref,
          name: String(zv),
          legendgroup: String(zv),
          offsetgroup: `${cellGroup}::${zv}`,
          alignmentgroup: cellGroup,
          showlegend: showLegend,
          box: { visible: true },
          meanline: { visible: true },
          points: 'outliers',
          marker: { color: c, opacity: 0.8 },
          line: { color: c, width: 1 },
          fillcolor: c + '50',
          spanmode: 'soft',
          customdata: grp,
        });
        i++;
      }
    } else {
      // No Z grouping: single trace, one violin per Y category, each
      // centered on its tick.
      const c = PALETTE[0];
      traces.push({
        type: 'violin',
        orientation: 'h',
        x: rows.map(mapX),
        y: rows.map((d) => String(d[yKey])),
        xaxis: xref,
        yaxis: yref,
        name: axisTitle(xField),
        offsetgroup: cellGroup,
        alignmentgroup: cellGroup,
        showlegend: false,
        box: { visible: true },
        meanline: { visible: true },
        points: 'outliers',
        marker: { color: c, opacity: 0.8 },
        line: { color: c, width: 1 },
        fillcolor: c + '50',
        spanmode: 'soft',
        customdata: rows,
      });
    }
    return traces;
  }

  if (zField && zField.isCategorical) {
    const zKey = zField.name;
    const zVals = zCategoryValues || distinctSortedValues(rows, zKey, { includeNull: true });
    let i = 0;
    for (const zv of zVals) {
      const grp = zGroups?.get(zv) || rowsForZ(rows, zKey, zv);
      const c = PALETTE[i % PALETTE.length];
      traces.push({
        type: 'violin',
        x: grp.map((d) => String(d[xKey])),
        y: grp.map((d) => d[yKey]),
        xaxis: xref,
        yaxis: yref,
        name: String(zv),
        legendgroup: String(zv),
        offsetgroup: `${cellGroup}::${zv}`,
        alignmentgroup: cellGroup,
        showlegend: showLegend,
        box: { visible: true },
        meanline: { visible: true },
        points: 'outliers',
        marker: { color: c, opacity: 0.8 },
        line: { color: c, width: 1 },
        fillcolor: c + '50',
        spanmode: 'soft',
        customdata: grp,
      });
      i++;
    }
  } else {
    // No Z grouping: emit a SINGLE trace. One-trace-per-X combined
    // with violinmode='group' offsets each violin into its own sub-
    // slot inside its category, so the violin drifts away from the X
    // tick. A single trace puts each violin centered on its tick.
    const c = PALETTE[0];
    traces.push({
      type: 'violin',
      x: rows.map((d) => String(d[xKey])),
      y: rows.map((d) => d[yKey]),
      xaxis: xref,
      yaxis: yref,
      name: axisTitle(yField),
      offsetgroup: cellGroup,
      alignmentgroup: cellGroup,
      showlegend: false,
      box: { visible: true },
      meanline: { visible: true },
      points: 'outliers',
      marker: { color: c, opacity: 0.8 },
      line: { color: c, width: 1 },
      fillcolor: c + '50',
      spanmode: 'soft',
      customdata: rows,
    });
  }
  return traces;
}

// Swarm (beeswarm) plot for one cell.
//
// Each X tick owns a horizontal "slot" of width 1 on the (forced) linear
// axis. Within a slot every sample is placed at its true Y; X is offset
// DETERMINISTICALLY by local density — same-Y points stack side by side in
// ~1-dot steps, so dense regions fan out into a violin-like silhouette
// while every dot stays a real sample. Slots target ~10 dot diameters of
// physical width (see the swarm width rule in buildUnifiedSpec).
//
// Above SWARM_GL_THRESHOLD rows we render via scattergl (WebGL) instead of
// scatter (SVG) — pan/zoom/hover stops thrashing the DOM tree once the
// total marker count climbs into the tens of thousands.
//
// Points are opaque with no outline: overlapping dots must not darken the
// color. Per-Z-group median lines (same color, bold, with a light halo
// casing so the line stays readable over dense point clouds) are drawn on
// top, and each slot's median value is annotated at the top of the cell.
//
// First version: only numeric Y. Categorical Y is reported via a warning
// so the caller can prompt the user to switch chart.
const SWARM_GL_THRESHOLD = 5000;
function buildSwarmTraces({ rows, totalRowCount, xField, yField, zField, axisRef, zCategoryValues, showLegend, xCategoryArray, swarmStep }) {
  const xKey = xField.name;
  const yKey = yField.name;
  const xref = `x${axisRef}`;
  const yref = `y${axisRef}`;
  const yIsCat = !!yField.isCategorical;
  const traces = [];
  // 顶部中位数标注由调用方并入 layout.annotations（builder 契约只回 traces 数组）
  (traces as any).annotations = [];
  const medianTraces = [];

  if (yIsCat) return traces;

  // 阈值按全量行数判定：分面时 rows 只是块内子集，用错会让 N 块各退化成 SVG。
  const useGl = (totalRowCount ?? rows.length) >= SWARM_GL_THRESHOLD;
  const traceType = useGl ? 'scattergl' : 'scatter';

  const xCats = xCategoryArray || [];
  const xIndexOf = new Map(xCats.map((v, i) => [v, i]));

  const N_BINS = 30;

  let yMin = Infinity, yMax = -Infinity;
  for (const r of rows) {
    const v = r[yKey];
    if (!Number.isFinite(v)) continue;
    if (v < yMin) yMin = v;
    if (v > yMax) yMax = v;
  }
  if (!Number.isFinite(yMin) || !Number.isFinite(yMax)) return traces;
  const yRange = (yMax - yMin) || 1;
  const binWidth = yRange / N_BINS;

  // Place a group of rows at (cx, slotW): returns swarmed coordinates +
  // original rows kept aligned so customdata still resolves on hover.
  // 居中对称堆叠：同一 y 箱内的点以约一个点径的步长横向铺开（档位目标
  // 宽度 50px ≈ 10 点径），最宽不超过 (子)槽位的 90%。
  const swarmGroup = (group, cx, slotW) => {
    const xs = new Array(group.length);
    const ys = new Array(group.length);
    if (group.length === 0) return { xs, ys };
    const bins = new Map();
    for (let i = 0; i < group.length; i++) {
      const v = group[i][yKey];
      ys[i] = v;
      if (!Number.isFinite(v)) { xs[i] = cx; continue; }
      let b = Math.floor((v - yMin) / binWidth);
      if (b >= N_BINS) b = N_BINS - 1;
      if (b < 0) b = 0;
      const bin = bins.get(b);
      if (bin) bin.push(i);
      else bins.set(b, [i]);
    }
    let maxBin = 1;
    for (const idxs of bins.values()) if (idxs.length > maxBin) maxBin = idxs.length;
    const step = swarmStep ?? Math.min(0.1, (slotW * 0.9) / maxBin);
    for (const idxs of bins.values()) {
      idxs.forEach((i, j) => { xs[i] = cx + (j - (idxs.length - 1) / 2) * step; });
    }
    return { xs, ys };
  };

  // Pre-bucket rows by (X category, Z value) — single O(n) scan vs.
  // O(n × |X| × |Z|) repeated filters.
  const hasZ = !!(zField && zField.isCategorical);
  const zKey = hasZ ? zField.name : null;
  const groupKey = (xc, zv) => `${xc}${zv}`;
  const groups = new Map();
  for (const r of rows) {
    const xc = String(r[xKey]);
    if (!xIndexOf.has(xc)) continue;
    const zv = hasZ
      ? (isNullish(r[zKey]) ? NULL_KEY : r[zKey])
      : '__all__';
    const k = groupKey(xc, zv);
    let arr = groups.get(k);
    if (!arr) { arr = []; groups.set(k, arr); }
    arr.push(r);
  }

  const zVals = hasZ ? (zCategoryValues || distinctSortedValues(rows, zKey, { includeNull: true })) : ['__all__'];
  const nZ = zVals.length || 1;
  // 中位数数值标注：标注位过多时转竖排，超过 200 个直接省略（hover 仍可读）
  const labelMode = xCats.length * nZ > 200 ? 'off'
    : xCats.length * nZ > 40 ? 'rotated' : 'flat';

  // 每 Z 组一条中位数折线：同色加粗 + 亮色描边，把趋势线从点云中分离出来
  const pushMedianLayer = (medians, color, extra) => {
    if (!medians.length) return;
    const mx = medians.map((m) => m.x);
    const my = medians.map((m) => m.y);
    medianTraces.push({
      type: 'scatter',
      mode: 'lines',
      x: mx,
      y: my,
      xaxis: xref,
      yaxis: yref,
      name: `${extra} 中位数描边`,
      showlegend: false,
      connectgaps: true,
      line: { color: 'rgba(232,235,240,0.55)', width: 4.5 },
      hoverinfo: 'skip',
      meta: { analysisLayer: 'swarm-median' },
    });
    medianTraces.push({
      type: 'scatter',
      mode: 'lines+markers',
      x: mx,
      y: my,
      xaxis: xref,
      yaxis: yref,
      name: `${extra} 中位数`,
      showlegend: false,
      connectgaps: true,
      customdata: medians.map((m) => ({ count: m.count })),
      line: { color, width: 2.5 },
      marker: { color, size: 5, symbol: 'circle' },
      hovertemplate: `<b>${axisTitle(yField)} 中位数</b>: %{y}<br>n=%{customdata.count}<extra>${extra}</extra>`,
      meta: { analysisLayer: 'swarm-median' },
    });
    if (labelMode === 'off') return;
    for (const m of medians) {
      (traces as any).annotations.push({
        xref,
        yref: `${yref} domain`,
        x: m.x,
        y: 0.995,
        yanchor: 'top',
        textangle: labelMode === 'rotated' ? -90 : 0,
        text: formatMedianLabel(m.y),
        showarrow: false,
        font: { size: 9, color },
        bgcolor: 'rgba(24,27,33,0.65)',
        borderpad: 1,
      });
    }
  };

  if (hasZ) {
    const subSlotW = 1 / nZ;
    let i = 0;
    for (const zv of zVals) {
      const c = PALETTE[i % PALETTE.length];
      const zCenter = -0.5 + (i + 0.5) * subSlotW;
      const allXs = [];
      const allYs = [];
      const allCustom = [];
      const medians = [];
      for (const xc of xCats) {
        const grp = groups.get(groupKey(xc, zv)) || [];
        const tickIdx = xIndexOf.get(xc);
        if (tickIdx == null) continue;
        const { xs, ys } = swarmGroup(grp, (tickIdx as number) + (zCenter as number), subSlotW as number);
        allXs.push(...xs);
        allYs.push(...ys);
        allCustom.push(...grp);
        const vals = grp.map((r) => r[yKey]).filter((v) => Number.isFinite(v));
        if (vals.length) {
          medians.push({ x: (tickIdx as number) + (zCenter as number), y: medianOf(vals), count: vals.length });
        }
      }
      traces.push({
        type: traceType,
        mode: 'markers',
        x: allXs,
        y: allYs,
        xaxis: xref,
        yaxis: yref,
        name: String(zv),
        legendgroup: String(zv),
        showlegend: showLegend,
        marker: { size: 5, color: c, opacity: 1 },
        customdata: allCustom,
        hovertemplate: `<b>${axisTitle(xField)}</b>=%{customdata.${xKey}}<br>${axisTitle(yField)}=%{y}<extra>${zv}</extra>`,
      });
      pushMedianLayer(medians, c, String(zv));
      i++;
    }
  } else {
    const c = PALETTE[0];
    const allXs = [];
    const allYs = [];
    const allCustom = [];
    const medians = [];
    for (const xc of xCats) {
      const grp = groups.get(groupKey(xc, '__all__')) || [];
      const tickIdx = xIndexOf.get(xc);
      if (tickIdx == null) continue;
      const { xs, ys } = swarmGroup(grp, tickIdx, 1);
      allXs.push(...xs);
      allYs.push(...ys);
      allCustom.push(...grp);
      const vals = grp.map((r) => r[yKey]).filter((v) => Number.isFinite(v));
      if (vals.length) medians.push({ x: tickIdx, y: medianOf(vals), count: vals.length });
    }
    traces.push({
      type: traceType,
      mode: 'markers',
      x: allXs,
      y: allYs,
      xaxis: xref,
      yaxis: yref,
      name: axisTitle(yField),
      showlegend: false,
      marker: { size: 5, color: c, opacity: 1 },
      customdata: allCustom,
      hovertemplate: `<b>${axisTitle(xField)}</b>=%{customdata.${xKey}}<br>${axisTitle(yField)}=%{y}<extra></extra>`,
    });
    pushMedianLayer(medians, c, '');
  }

  traces.push(...medianTraces);
  return traces;
}

// Line plot for one cell. Within each Z group, points are sorted by X
// so the line is monotone left→right (rather than connecting points in
// trace-array order, which gives a tangled mess).
//
// If Y is categorical, lines have no real meaning → fall back to markers only.
//
// Whenever Y is numeric we collapse rows to their mean per X (per Z group)
// to give one point per X — matching the customer reference plot's smooth
// one-point-per-tick lines. Without this collapse the line zigzags wildly
// within each X bucket (multiple devices may share the same X value
// regardless of whether X is categorical or continuous).
//
// `xCategoryArray` (when xIsCat) gives the visual tick order; we sort by
// indexOf into that array so the line follows the on-screen tick order.
function buildLineTraces({ rows, xField, yField, zField, axisRef, zCategoryValues, zGroups, showLegend, xCategoryArray }) {
  const xKey = xField.name;
  const yKey = yField.name;
  const xref = `x${axisRef}`;
  const yref = `y${axisRef}`;
  const xIsCat = !!xField.isCategorical;
  const yIsCat = !!yField.isCategorical;
  const lineMode = yIsCat ? 'markers' : 'lines+markers';
  const traces = [];

  const sortRows = (arr) => {
    if (xIsCat) {
      // Sort by position in the layout's categoryarray so the line
      // matches the visual tick order. Unknown values go to the end.
      const cats = xCategoryArray || [];
      const idx = (v) => {
        const i = cats.indexOf(String(v));
        return i < 0 ? Number.POSITIVE_INFINITY : i;
      };
      return arr.slice().sort((a, b) => idx(a[xKey]) - idx(b[xKey]));
    }
    return arr.slice().sort((a, b) => {
      const ax = a[xKey], bx = b[xKey];
      if (!isNum(ax)) return 1;
      if (!isNum(bx)) return -1;
      return ax - bx;
    });
  };

  // Collapse rows to mean Y per X (used whenever Y is numeric; works for
  // both categorical and continuous X). Without this, a numeric X with
  // many devices sharing the same value produces a vertical zigzag.
  const meanByX = (arr) => {
    const acc = new Map(); // xVal → { sum, n }
    for (const r of arr) {
      const xv = r[xKey];
      const yv = r[yKey];
      if (xv === null || xv === undefined || !isNum(yv)) continue;
      const slot = acc.get(xv) || { sum: 0, n: 0, memberRowIds: [] };
      slot.sum += yv;
      slot.n += 1;
      const rowId = r.rowId ?? r.__rowId;
      if (rowId) slot.memberRowIds.push(rowId);
      acc.set(xv, slot);
    }
    if (xIsCat) {
      const cats = xCategoryArray || [];
      const idx = (v) => {
        const i = cats.indexOf(String(v));
        return i < 0 ? Number.POSITIVE_INFINITY : i;
      };
      const out = [];
      for (const [xv, { sum, n, memberRowIds }] of acc) {
        out.push({ [xKey]: xv, [yKey]: sum / n, __memberRowIds: memberRowIds });
      }
      return out.sort((a, b) => idx(a[xKey]) - idx(b[xKey]));
    }
    const out = [];
    for (const [xv, { sum, n, memberRowIds }] of acc) {
      out.push({ [xKey]: xv, [yKey]: sum / n, __memberRowIds: memberRowIds });
    }
    return out.sort((a, b) => a[xKey] - b[xKey]);
  };

  // Build a single trace from a row subset. When Y is numeric we collapse
  // duplicate-X rows to their mean; otherwise we just sort.
  const collapse = !yIsCat;
  const prep = collapse ? meanByX : sortRows;

  if (zField && zField.isCategorical) {
    const zKey = zField.name;
    const zVals = zCategoryValues || distinctSortedValues(rows, zKey, { includeNull: true });
    let i = 0;
    for (const zv of zVals) {
      // zVals 含 NULL_KEY 时，对应行的 r[zKey] 是 null/undefined，不是字符串 '∅'。
      // rowsForZ 替我们处理这个映射；用 === 直接比会让 NULL 桶静默掉光。
      const grp = prep(zGroups?.get(zv) || rowsForZ(rows, zKey, zv));
      const c = PALETTE[i % PALETTE.length];
      traces.push({
        type: 'scatter',
        mode: lineMode,
        x: grp.map((d) => d[xKey]),
        y: grp.map((d) => d[yKey]),
        xaxis: xref,
        yaxis: yref,
        name: String(zv),
        legendgroup: String(zv),
        showlegend: showLegend,
        line: { color: c, width: 1.4 },
        marker: { color: c, size: yIsCat ? 6 : 5 },
        customdata: grp,
      });
      i++;
    }
  } else {
    const sorted = prep(rows);
    traces.push({
      type: 'scatter',
      mode: lineMode,
      x: sorted.map((d) => d[xKey]),
      y: sorted.map((d) => d[yKey]),
      xaxis: xref,
      yaxis: yref,
      name: 'all',
      showlegend: false,
      line: { color: PALETTE[0], width: 1.4 },
      marker: { color: PALETTE[0], size: yIsCat ? 6 : 5 },
      customdata: sorted,
    });
  }
  return traces;
}

const BUILDER_MAP = {
  scatter: buildScatterTraces,
  box: buildBoxTraces,
  violin: buildViolinTraces,
  swarm: buildSwarmTraces,
  line: buildLineTraces,
};

// Chart types that ignore numeric Z (box/violin/swarm/line).
const Z_NUMERIC_UNSUPPORTED = new Set(['box', 'violin', 'swarm', 'line']);

// Base the decision on emitted points across all panels. A 5k-row dataset
// rendered in eight metric panels is already far beyond a comfortable SVG DOM.
const PERF_GL_THRESHOLD = 5000;
// Hard ceiling on the number of distinct X categorical ticks per cell.
// Beyond this Plotly renders unusably (browser stalls, ticks overlap).
// We surface a warning and clamp the categoryarray to the head.
const X_CATEGORY_TICK_LIMIT = 80;

// 分面模式（facetZ）下 Z 值个数上限：超出截断 + 警告，
// 防止几十个竖排子图把单张 Plotly 图撑到不可用。
const MAX_Z_FACETS = 24;

// buildUnifiedSpec 的参数；UnifiedChartGrid 的 props = UnifiedSpecArgs + 回调
interface UnifiedSpecArgs {
  chartType: string;
  rows: Record<string, unknown>[];
  xFields: ChartField[];
  yFields: ChartField[];
  zField?: ChartField | null;
  facetZ?: boolean;
  height?: number;
  width?: number | null;
  analysisLayout?: boolean;
  datasetRevision?: number | string;
  showDataPoints?: boolean;
  interactionMode?: 'zoom' | 'pan' | 'select' | 'lasso';
}

// Pure chart-spec builder extracted from UnifiedChartGrid's useMemo so the
// trace/layout computation can be unit-tested without rendering Plotly.
// Behavior must stay identical to the original inline computation.
export function buildUnifiedSpec({
  chartType,
  rows,
  xFields,
  yFields,
  zField,
  facetZ = false, // true 且 Z 为类别字段时：每个 Z 值一块竖排子图（分面），块内不再颜色编码
  height = 350,
  width = null,
}: UnifiedSpecArgs) {
  const cols = (xFields || []).length;
  const nRows = (yFields || []).length;
  const builder = BUILDER_MAP[chartType];
  const placeholder = cols === 0 || nRows === 0
    ? '请至少选一个 X / Y 字段'
    : !builder
      ? `未知图表类型：${String(chartType)}`
      : null;

  const pw = [];
  // box/violin/swarm/line 不支持数值 Z —— 丢弃并告警，两处判定必须同源。
  const dropNumericZ = !!zField && Z_NUMERIC_UNSUPPORTED.has(chartType) && !zField.isCategorical;
  const effZ = dropNumericZ ? null : zField || null;
  if (dropNumericZ && zField) {
    pw.push({ kind: 'z_numeric_dropped', chartType, zField: zField.name });
  }

  // ── 按 Z 值分面：仅类别 Z；数值 Z、未开 facetZ 或空数据时 nZ=1，退化为现状 ──
  const faceting = !!facetZ && !!effZ && !!effZ.isCategorical && rows.length > 0;
  let zFacetValues: any[] = [null];
  if (faceting && effZ) {
    const all = distinctSortedValues(rows, effZ.name, { includeNull: true });
    if (all.length > MAX_Z_FACETS) {
      zFacetValues = all.slice(0, MAX_Z_FACETS);
      pw.push({ kind: 'z_facets_clamped', zField: effZ.name, total: all.length, shown: MAX_Z_FACETS });
    } else {
      zFacetValues = all;
    }
  }
  const nZ = faceting ? zFacetValues.length : 1;
  const totalRows = nRows * nZ;

  const estimatedPointCount = rows.length * Math.max(1, cols * nRows);
  const useGl = estimatedPointCount >= PERF_GL_THRESHOLD;
  const pWarn = useGl
    ? { rowCount: rows.length, pointCount: estimatedPointCount, threshold: PERF_GL_THRESHOLD }
    : null;

  const zCategoryValues = effZ && effZ.isCategorical
    ? distinctSortedValues(rows, effZ.name, { includeNull: true })
    : null;
  const zGroups = effZ && effZ.isCategorical ? groupBy(rows, effZ.name) : null;
  const categoryCache = new Map<string, string[]>();
  const rangeCache = new Map<string, number[] | null>();
  const categoriesFor = (field: ChartField) => {
    let values = categoryCache.get(field.name);
    if (!values) {
      values = distinctSortedValues(rows, field.name).map(String);
      categoryCache.set(field.name, values);
    }
    return values;
  };
  const rangeFor = (field: ChartField) => {
    if (!rangeCache.has(field.name)) rangeCache.set(field.name, numericRange(rows, field.name));
    return rangeCache.get(field.name) || null;
  };

  // 全指标、档位和Z组共用步长，避免每个组都被单独拉宽到满槽而丢失密度比例。
  let swarmStep: number | undefined;
  if (chartType === 'swarm') {
    let widest = 1;
    for (const yField of yFields) {
      if (yField.isCategorical) continue;
      const values = rows.map((row) => row[yField.name]).filter(Number.isFinite) as number[];
      if (!values.length) continue;
      const low = values.reduce((left, right) => Math.min(left, right), Infinity);
      const high = values.reduce((left, right) => Math.max(left, right), -Infinity);
      const binWidth = (high - low || 1) / 30;
      for (const xField of xFields) {
        const bins = new Map<string, number>();
        for (const row of rows) {
          const value = row[yField.name];
          if (typeof value !== 'number' || !Number.isFinite(value)) continue;
          const bin = Math.max(0, Math.min(29, Math.floor((value - low) / binWidth)));
          const key = JSON.stringify([String(row[xField.name]), effZ ? row[effZ.name] ?? NULL_KEY : NULL_KEY, bin]);
          const count = (bins.get(key) ?? 0) + 1;
          bins.set(key, count);
          widest = Math.max(widest, count);
        }
      }
    }
    const groupCount = faceting ? 1 : Math.max(1, zCategoryValues?.length ?? 1);
    swarmStep = Math.min(0.1, 0.9 / groupCount / widest);
  }

  const hasNumericZ = effZ && !effZ.isCategorical && chartType === 'scatter';
  const outTraces: any[] = [];
  const outLayout: any = {
    ...baseLayout,
    grid: {
      rows: totalRows,
      columns: cols,
      pattern: 'independent',
      xgap: 0.08,
      // plotly 要求 (rows-1)*ygap < 1；分面时行数常超 7，
      // 按总行数收缩使像素间距近似恒定（≈112px/行高350px）。
      ygap: chartType === 'swarm' ? Math.min(0.03, 0.32 / totalRows)
        : faceting ? Math.min(0.16, 0.32 / totalRows) : 0.16,
      roworder: 'top to bottom',
    },
    margin: { l: 60, r: hasNumericZ ? 120 : 60, t: 60, b: 60 },
    height: totalRows * height + 100,
    annotations: [],
    showlegend: !!effZ && effZ.isCategorical && !faceting,
    legend: { orientation: 'h', y: 1.04, x: 0 },
    font: { ...baseLayout.font, size: 11 },
    boxmode: 'group',
    violinmode: 'group',
  };
  if (width) outLayout.width = width;

  const COLOR_OWNER_CELL = 1;

  if (!placeholder) for (let zi = 0; zi < nZ; zi++) {
    const zv = faceting ? zFacetValues[zi] : null;
    const cellRows = faceting && effZ ? (zGroups?.get(zv) || []) : rows;

    for (let r = 0; r < nRows; r++) {
      const yField = yFields[r];
      for (let c = 0; c < cols; c++) {
        const xField = xFields[c];
        const cellIdx = (zi * nRows + r) * cols + c + 1;
        const sfx = axisSuffix(cellIdx);
        const xAxisKey = `xaxis${sfx}`;
        const yAxisKey = `yaxis${sfx}`;

        const yIsCat = !!yField.isCategorical;

        const xLayout: any = {
          ...baseLayout.xaxis,
          title: { text: axisTitle(xField), font: { size: 11 } },
          tickfont: { size: 10 },
          automargin: true,
        };
        const xAsCategory = !!xField.isCategorical
          || ((chartType === 'box' || chartType === 'violin' || chartType === 'swarm') && !yIsCat);
        let xCategoryArray = null;
        if (xAsCategory) {
          // 类目轴的 tick 集合始终按全量 rows 计算：各 Z 块共享同一套
          // 类目顺序，避免块间 tick 错位、缺失类目整块塌掉。
          const allCats = categoriesFor(xField);
          if (allCats.length > X_CATEGORY_TICK_LIMIT) {
            xCategoryArray = allCats.slice(0, X_CATEGORY_TICK_LIMIT);
            pw.push({
              kind: 'x_categories_clamped',
              xField: xField.name,
              total: allCats.length,
              shown: X_CATEGORY_TICK_LIMIT,
            });
          } else {
            xCategoryArray = allCats;
          }
          if (chartType === 'swarm' || (chartType === 'scatter' && useGl)) {
            xLayout.type = 'linear';
            xLayout.tickmode = 'array';
            xLayout.tickvals = xCategoryArray.map((_, i) => i);
            xLayout.ticktext = xCategoryArray;
            xLayout.range = [-0.5, xCategoryArray.length - 0.5];
          } else {
            xLayout.type = 'category';
            xLayout.categoryorder = 'array';
            xLayout.categoryarray = xCategoryArray;
          }
          // 蜂群图用档位间实线框线分隔区间，刻度处的竖网格线会与之打架，关掉
          if (chartType === 'swarm') xLayout.showgrid = false;
        }

        // 分面 small-multiples：X 刻度与轴标题只显示在最底部一块；数值 X
        // 用显式并集 range 让同列各块量程一致（类目轴 categoryarray 本就
        // 全量共享）。plotly 每根轴标签/标题逐轴独立渲染，按轴隐藏即可。
        if (faceting) {
          const isBottomOfColumn = zi === nZ - 1 && r === nRows - 1;
          if (!isBottomOfColumn) {
            xLayout.showticklabels = false;
            xLayout.title = undefined;
          }
          if (!xAsCategory) {
            const xr = rangeFor(xField);
            if (xr) xLayout.range = xr;
          }
        }

        if (chartType === 'swarm') {
          xLayout.matches = zi === 0 && r === 0 ? undefined : `x${axisSuffix(c + 1)}`;
          if (zi !== nZ - 1 || r !== nRows - 1) {
            xLayout.showticklabels = false;
            xLayout.title = undefined;
          }
        }

        outLayout[xAxisKey] = xLayout;

        const yLayout = {
          ...baseLayout.yaxis,
          title: { text: axisTitle(yField), font: { size: 11 } },
          tickfont: { size: 10 },
          automargin: true,
        };
        if (yIsCat) {
          const yCats = categoriesFor(yField);
          yLayout.type = 'category';
          yLayout.categoryorder = 'array';
          yLayout.categoryarray = yCats;
        }
        outLayout[yAxisKey] = yLayout;

        outLayout.annotations.push({
          xref: `x${sfx} domain`,
          yref: `y${sfx} domain`,
          x: 0.5,
          y: 1.04,
          text: `${axisTitle(yField)} / ${axisTitle(xField)}`,
          showarrow: false,
          font: { size: 12, color: '#334155' },
        });

        if (chartType === 'swarm' && yIsCat) {
          pw.push({ kind: 'swarm_y_categorical', yField: yField.name });
        }

        const cellTraces = builder({
          rows: cellRows,
          totalRowCount: rows.length, // 供 GL/SVG 阈值按全量数据规模判定（分面时 rows 只是块内行数）
          xField,
          yField,
          zField: faceting ? null : effZ,
          axisRef: sfx,
          useGl,
          zCategoryValues,
          zGroups: faceting ? null : zGroups,
          showLegend: !faceting && cellIdx === COLOR_OWNER_CELL,
          cellGroup: `cell${cellIdx}`,
          xCategoryArray,
          swarmStep,
        });
        outTraces.push(...cellTraces);
        // 蜂群图的顶部中位数标注挂在返回数组的 annotations 属性上（builder 契约只回 traces）
        const cellAnnotations = (cellTraces as any).annotations;
        if (Array.isArray(cellAnnotations) && cellAnnotations.length) {
          outLayout.annotations.push(...cellAnnotations);
        }
      }
    }

    // 块级标题：每个 Z 值块顶部居中。锚定该块首 cell 的 y 域，
    // y>1 表示在域上方（y=1.14 为经验起始值，目视偏了再调）。
    if (faceting && effZ) {
      const sfx0 = axisSuffix(zi * nRows * cols + 1);
      outLayout.annotations.push({
        xref: 'paper',
        x: 0.5,
        xanchor: 'center',
        yref: `y${sfx0} domain`,
        y: 1.14,
        yanchor: 'bottom',
        text: `${axisTitle(effZ)} = ${zv === NULL_KEY ? '∅（缺失）' : String(zv)}`,
        showarrow: false,
        font: { size: 13, color: '#E7E9ED' },
      });
    }
  }

  // 蜂群图统一风格：档位间竖向实线框线贯穿整列子图；档位物理宽度目标约
  // 10 个点径（marker 5px × 10 = 50px），档数多时加宽整图交由容器横向滚动。
  // ponytail: width 为空时容器实际像素不可得，1100 是按桌面端典型图表区宽度的保守估计。
  if (chartType === 'swarm' && !placeholder && cols > 0) {
    const numericRow = yFields.findIndex((f) => !f.isCategorical);
    if (numericRow >= 0) {
      const slotCounts = xFields.map((f) => Math.min(categoriesFor(f).length, X_CATEGORY_TICK_LIMIT));
      slotCounts.forEach((slotCount, c) => {
        if (slotCount < 2) return;
        const frameAxis = `x${axisSuffix(((nZ - 1) * nRows + numericRow) * cols + c + 1)}`;
        for (let boundary = 0; boundary < slotCount - 1; boundary += 1) {
          outLayout.shapes = [...(outLayout.shapes || []), {
            type: 'line',
            xref: frameAxis,
            yref: 'paper',
            x0: boundary + 0.5,
            x1: boundary + 0.5,
            y0: 0,
            y1: 1,
            line: { color: 'rgba(196,200,208,0.28)', width: 1, dash: 'solid' },
          }];
        }
      });
      const maxSlots = Math.max(0, ...slotCounts);
      const swarmMinWidth = cols * maxSlots * 50 + outLayout.margin.l + outLayout.margin.r;
      if (swarmMinWidth > (width ?? 1100)) outLayout.width = swarmMinWidth;
    }
  }

  // 同一警告在 (zi × r × c) 循环里按块重复 push（分面时最多 nZ 份）——
  // emit 是整体替换语义、父组件不再去重，这里按内容去重后再返回。
  const dedupedWarns = [...new Map(pw.map((w) => [JSON.stringify(w), w])).values()];
  return { allTraces: outTraces, layout: outLayout, pendingWarns: dedupedWarns, perfWarn: pWarn, placeholder };
}

export function UnifiedChartGrid({
  plotId = 'chart-unified',
  chartType,
  rows,
  xFields,
  yFields,
  zField,
  facetZ = false,
  height = 350,
  width = null,
  onWarningsChange,
  onPointClick,
  onSelection,
  onDeselect,
  selectedRowIds,
  pointStyles,
  autorangeRequest = 0,
  revision,
  analysisLayout = false,
  datasetRevision,
  showDataPoints = true,
  interactionMode = 'select',
  yAxisRanges,
  onYAxisReset,
}: UnifiedSpecArgs & {
  plotId?: string;
  onWarningsChange?: (warns: any[]) => void;
  onPointClick?: (d: unknown, event: any) => void;
  onSelection?: (e: unknown) => void;
  onDeselect?: () => void;
  selectedRowIds?: Set<string>;
  pointStyles?: Record<string, PointStyleOverride>;
  autorangeRequest?: number;
  revision?: string;
  yAxisRanges?: YAxisRanges;
  onYAxisReset?: () => void;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const graphDivRef = useRef<any>(null);
  const pointerRef = useRef<{ x: number; y: number; id: number; pointClicked: boolean } | null>(null);
  const blankClickFrameRef = useRef(0);
  const [containerWidth, setContainerWidth] = useState<number | null>(null);
  const sceneWidth = width ?? (chartType === 'swarm' ? containerWidth : null);
  const pendingYAxisAutorangeRef = useRef(false);
  const previousRowsRef = useRef({ datasetId: revision, rowCount: rows.length });
  if (analysisLayout
    && previousRowsRef.current.datasetId === revision
    && previousRowsRef.current.rowCount !== rows.length) {
    pendingYAxisAutorangeRef.current = true;
  }
  previousRowsRef.current = { datasetId: revision, rowCount: rows.length };
  // Memoize the heavy trace+layout computation so it only re-runs when
  // the data or field configuration actually changes.
  const { allTraces, layout, pendingWarns, perfWarn, placeholder } = useMemo(
    () => analysisLayout
      ? buildCachedJmpChartScene(
        revision,
        { chartType, rows, xFields, yFields, zField, datasetRevision, showDataPoints, height, width: sceneWidth },
      )
      : buildUnifiedSpec({ chartType, rows, xFields, yFields, zField, facetZ, height, width: sceneWidth }),
    [analysisLayout, chartType, rows, xFields, yFields, zField, facetZ, height, sceneWidth, datasetRevision, showDataPoints],
  );
  const automaticYRanges = useMemo(() => buildYDataRanges(rows, yFields), [rows, yFields]);
  const plotLayout = useMemo(() => {
    const automaticAxes = buildYAxisLayoutRanges(layout, yFields, automaticYRanges);
    const axes = buildYAxisLayoutRanges(layout, yFields, { ...automaticYRanges, ...yAxisRanges });
    return {
      ...layout,
      ...Object.fromEntries(Object.entries(axes).map(([key, range]) =>
        [key, { ...layout[key], autorange: false, range }])),
      meta: { ...layout.meta, analysisYRanges: automaticAxes, analysisActiveYRanges: axes },
      ...(chartType === 'swarm' && layout.width ? { autosize: false } : {}),
      dragmode: interactionMode,
      // Row selection is owned by the workspace; avoid a second Plotly click selection.
      clickmode: 'event',
      uirevision: revision || 'analysis',
    };
  }, [layout, revision, interactionMode, chartType, automaticYRanges, yAxisRanges, yFields]);
  const plotConfig = useMemo(() => analysisLayout ? {
    ...baseConfig,
    displayModeBar: false,
    scrollZoom: true,
    doubleClick: false,
  } : { ...baseConfig, displayModeBar: false, doubleClick: false }, [analysisLayout]);
  const hasAppliedPointStylesRef = useRef(false);
  const syncPointStyles = useCallback((graphDiv: any) => {
    const hasStyles = Object.keys(pointStyles || {}).length > 0;
    if (!hasStyles && !hasAppliedPointStylesRef.current) return;
    hasAppliedPointStylesRef.current = hasStyles;
    applyPlotPointStyles(graphDiv, allTraces, pointStyles);
  }, [allTraces, pointStyles]);

  // 警告下发用「整体替换」语义：把当前完整警告数组交给父组件替换（而非逐条
  // 追加），父组件因此不需要 reset effect —— 旧实现的子先父后 passive
  // effect 顺序会在"rows 变而警告内容不变"时把警告永久清空。deps 含 rows：
  // rows 引用变而 warnsKey 相同（连续两次相同查询）时也必须重发 —— 父组件
  // 在 run() 开头已清空，靠这次重发恢复（探针实证：缺 rows 则警告永久丢失）。
  const warnsKey = JSON.stringify(pendingWarns);
  const perfKey = perfWarn ? perfWarn.rowCount : 0;
  useEffect(() => {
    if (!onWarningsChange) return;
    const ws = perfWarn ? [...pendingWarns, { kind: 'perf_gl', ...perfWarn }] : pendingWarns;
    queueMicrotask(() => onWarningsChange(ws));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [warnsKey, perfKey, rows, onWarningsChange]);

  const handleClick = useCallback((e) => {
    const p = e.points && e.points[0];
    const pointer = pointerRef.current;
    const graph = graphDivRef.current;
    if (p && pointer && graph && p.xaxis?.d2p && p.yaxis?.d2p) {
      const rect = graph.getBoundingClientRect();
      const x = rect.left + (p.xaxis._offset + p.xaxis.d2p(p.x))
        * rect.width / graph._fullLayout.width;
      const y = rect.top + (p.yaxis._offset + p.yaxis.d2p(p.y))
        * rect.height / graph._fullLayout.height;
      const size = Array.isArray(p.data?.marker?.size)
        ? p.data.marker.size[p.pointIndex] : p.data?.marker?.size;
      // Plotly throttles hover updates. A rapid blank click can otherwise reuse
      // the previous hovered point, especially in Electron.
      if (Number.isFinite(x) && Number.isFinite(y)
        && Math.hypot(x - pointer.x, y - pointer.y) > Math.max(8, (size || 6) / 2 + 3)) {
        const datum = nearestRawDatum(graph, pointer.x, pointer.y);
        if (datum) {
          pointer.pointClicked = true;
          onPointClick?.(datum, e);
        }
        return;
      }
    }
    if (p && pointerRef.current) pointerRef.current.pointClicked = true;
    if (!onPointClick) return;
    if (p?.data?.meta?.analysisLayer === 'swarm-median') return;
    if (p && p.customdata) onPointClick(p.customdata, e);
  }, [onPointClick]);

  useEffect(() => {
    applyPlotSelection(graphDivRef.current, selectedRowIds);
    if (!selectedRowIds?.size) clearPlotSelectionOutline(graphDivRef.current);
  }, [selectedRowIds]);

  useEffect(() => {
    syncPointStyles(graphDivRef.current);
  }, [syncPointStyles]);

  useEffect(() => {
    if (autorangeRequest > 0) applyAxesAutorange(graphDivRef.current, true);
  }, [autorangeRequest]);

  useEffect(() => {
    if (interactionMode !== 'select' && interactionMode !== 'lasso') {
      clearPlotSelectionOutline(graphDivRef.current);
    }
  }, [interactionMode]);

  const handleDeselect = useCallback(() => {
    clearPlotSelectionOutline(graphDivRef.current);
    onDeselect?.();
  }, [onDeselect]);

  const handleSelected = useCallback((event: any) => {
    // Clearing a layout selection or changing dragmode emits an empty selected
    // event without a region. It must not discard the workspace's row selection.
    if (!event?.points?.length && !event?.range && !event?.lassoPoints) return;
    onSelection?.(event);
  }, [onSelection]);

  useEffect(() => {
    // Plotly places a drag cover outside the chart while the pointer is down.
    // Listen at window level so clicks ending on that cover are still observed.
    const handlePointerUp = (event: PointerEvent) => {
      const pointer = pointerRef.current;
      if (!pointer || pointer.id !== event.pointerId) return;
      if (Math.hypot(event.clientX - pointer.x, event.clientY - pointer.y) > 4) {
        pointerRef.current = null;
        return;
      }
      blankClickFrameRef.current = requestAnimationFrame(() => {
        if (!pointer.pointClicked) handleDeselect();
        pointerRef.current = null;
      });
    };
    window.addEventListener('pointerup', handlePointerUp, true);
    return () => {
      cancelAnimationFrame(blankClickFrameRef.current);
      window.removeEventListener('pointerup', handlePointerUp, true);
    };
  }, [handleDeselect]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || typeof ResizeObserver === 'undefined') return;
    let frame = 0;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        if (chartType === 'swarm' && container.clientWidth > 0) {
          setContainerWidth(container.clientWidth);
        }
        if (graphDivRef.current) {
          getPlotly().then((Plotly) => Plotly.Plots.resize(graphDivRef.current));
        }
      });
    });
    observer.observe(container);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [chartType, placeholder]);

  if (placeholder) {
    return (
      <div style={{ padding: 24, color: 'var(--fg-4)', fontSize: 12 }}>
        {placeholder}
      </div>
    );
  }

  return (
    <div ref={containerRef} style={{ width: '100%', height: plotLayout.height }}
      onPointerDownCapture={(event) => {
        cancelAnimationFrame(blankClickFrameRef.current);
        pointerRef.current = event.button === 0
          && (event.target as Element).closest('.nsewdrag, .select-outline')
          ? { x: event.clientX, y: event.clientY, id: event.pointerId, pointClicked: false }
          : null;
      }}
      onPointerCancel={() => { pointerRef.current = null; }}
    >
      <PlotAny
        key={`${analysisLayout ? 'analysis' : 'generic'}:${chartType}`}
        divId={plotId}
        className={analysisLayout ? 'analysis-plot' : undefined}
        data={allTraces}
        layout={plotLayout}
        config={plotConfig}
        style={{ width: chartType === 'swarm' ? plotLayout.width ?? '100%' : '100%', height: plotLayout.height }}
        useResizeHandler
        onInitialized={(_figure, graphDiv) => {
          graphDivRef.current = graphDiv;
          syncPointStyles(graphDiv);
          applyPlotSelection(graphDiv, selectedRowIds);
        }}
        onUpdate={(_figure, graphDiv) => {
          graphDivRef.current = graphDiv;
        }}
        onAfterPlot={() => {
          if (!pendingYAxisAutorangeRef.current) return;
          pendingYAxisAutorangeRef.current = false;
          applyYAxisAutorange(graphDivRef.current);
        }}
        onClick={handleClick}
        onSelected={handleSelected}
        onDeselect={handleDeselect}
        onDoubleClick={() => {
          onYAxisReset?.();
          applyAxesAutorange(graphDivRef.current, true);
        }}
        onError={(error) => reportPlotlyError('react', error)}
      />
    </div>
  );
}
