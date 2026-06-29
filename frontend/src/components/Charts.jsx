import React, { useCallback, useEffect, useMemo } from 'react';
import Plot from 'react-plotly.js';

// Sentinel for null/undefined Z bucket — kept as a literal so the legend
// reads "∅" and groupBy() / distinctSortedValues() / per-trace filters all
// agree on the same key. Without this, rows with null Z fall out of
// every per-Z trace and silently disappear from the chart.
const NULL_KEY = '∅';
const isNullish = (v) => v === null || v === undefined;

const baseLayout = {
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
 *  ScatterPlot
 *
 *  - If xIsCategory, we render a strip-style scatter (one column of
 *    points per X value, with horizontal jitter).
 *  - colorKey can be categorical (one trace per value) or numeric
 *    (single trace, marker.color mapped via colorscale + colorbar).
 * ------------------------------------------------------------------ */
export function ScatterPlot({
  rows, xKey, yKey, colorKey,
  xLabel, yLabel, colorLabel,
  xIsCategory = false, colorIsCategory = true,
  onPointClick,
}) {
  const traces = useMemo(() => {
    const out = [];
    if (colorKey && !colorIsCategory) {
      // Numeric color mapping → single trace with colorscale.
      const xs = rows.map((d) => d[xKey]);
      const ys = rows.map((d) => d[yKey]);
      const cs = rows.map((d) => d[colorKey]);
      out.push({
        type: 'scattergl',
        mode: 'markers',
        x: xs,
        y: ys,
        marker: {
          size: 6,
          color: cs,
          colorscale: NUMERIC_COLORSCALE,
          showscale: true,
          colorbar: { title: { text: colorLabel || colorKey, side: 'right' }, thickness: 12, len: 0.6 },
          opacity: 0.85,
        },
        customdata: rows,
        hovertemplate: `<b>%{x}</b>, %{y}<br>${colorLabel || colorKey}: %{marker.color}<extra></extra>`,
        name: '全部',
      });
    } else {
      const colorGroups = colorKey ? groupBy(rows, colorKey) : new Map([['all', rows]]);
      const colorKeysSorted = colorKey ? sortKeys(colorGroups.keys()) : ['all'];
      let i = 0;
      for (const ck of colorKeysSorted) {
        const grp = colorGroups.get(ck);
        let xs, ys;
        if (xIsCategory) {
          xs = grp.map((d) => d[xKey]);
          ys = grp.map((d) => d[yKey]);
        } else {
          xs = grp.map((d) => d[xKey]);
          ys = grp.map((d) => d[yKey]);
        }
        out.push({
          type: xIsCategory ? 'scatter' : 'scattergl',
          mode: 'markers',
          x: xs,
          y: ys,
          name: colorKey ? `${ck}` : '全部',
          marker: {
            size: xIsCategory ? 6 : 5,
            color: PALETTE[i % PALETTE.length],
            opacity: 0.78,
            line: xIsCategory ? { width: 0.5, color: '#ffffff80' } : undefined,
          },
          customdata: grp,
          hovertemplate: `<b>%{x}</b>, %{y}<extra></extra>`,
        });
        i++;
      }
    }
    return out;
  }, [rows, xKey, yKey, colorKey, colorIsCategory, xLabel, yLabel, colorLabel]);

  const layout = useMemo(() => {
    const out = {
      ...baseLayout,
      showlegend: !!colorKey && colorIsCategory,
      xaxis: {
        ...baseLayout.xaxis,
        title: xLabel || xKey,
        type: xIsCategory ? 'category' : undefined,
      },
      yaxis: { ...baseLayout.yaxis, title: yLabel || yKey },
      legend: { orientation: 'h', y: 1.08, x: 0 },
    };
    if (xIsCategory) {
      out.xaxis.categoryorder = 'category ascending';
    }
    return out;
  }, [xLabel, yLabel, xKey, yKey, colorKey, colorIsCategory, xIsCategory]);

  const handleClick = useCallback((e) => {
    if (onPointClick && e.points && e.points[0]) {
      onPointClick(e.points[0].customdata);
    }
  }, [onPointClick]);

  return (
    <Plot
      divId="chart-scatter"
      data={traces}
      layout={layout}
      config={baseConfig}
      style={{ width: '100%', height: '100%' }}
      useResizeHandler
      onClick={handleClick}
    />
  );
}

/* ------------------------------------------------------------------
 *  BoxPlot
 *
 *  Group on X (categorical). If colorKey is also categorical, we
 *  render side-by-side boxes (boxmode: group) per color value.
 * ------------------------------------------------------------------ */
export function BoxPlot({ rows, xKey, yKey, colorKey, xLabel, yLabel, colorIsCategory = true }) {
  const traces = useMemo(() => {
    const out = [];
    if (colorKey && colorIsCategory && colorKey !== xKey) {
      const colorGroups = groupBy(rows, colorKey);
      const ckeys = sortKeys(colorGroups.keys());
      let i = 0;
      for (const ck of ckeys) {
        const grp = colorGroups.get(ck);
        out.push({
          type: 'box',
          x: grp.map((d) => d[xKey]),
          y: grp.map((d) => d[yKey]),
          name: String(ck),
          marker: { color: PALETTE[i % PALETTE.length] },
          boxpoints: 'outliers',
        });
        i++;
      }
    } else {
      const xGroups = groupBy(rows, xKey);
      const xkeys = sortKeys(xGroups.keys());
      let i = 0;
      for (const xk of xkeys) {
        const grp = xGroups.get(xk);
        out.push({
          type: 'box',
          x: grp.map(() => xk),
          y: grp.map((d) => d[yKey]),
          name: String(xk),
          marker: { color: PALETTE[i % PALETTE.length] },
          boxpoints: 'outliers',
        });
        i++;
      }
    }
    return out;
  }, [rows, xKey, yKey, colorKey, colorIsCategory]);

  const layout = useMemo(() => ({
    ...baseLayout,
    showlegend: !!colorKey && colorIsCategory && colorKey !== xKey,
    boxmode: 'group',
    xaxis: { ...baseLayout.xaxis, title: xLabel || xKey, type: 'category' },
    yaxis: { ...baseLayout.yaxis, title: yLabel || yKey },
    legend: { orientation: 'h', y: 1.08, x: 0 },
  }), [xLabel, yLabel, xKey, yKey, colorKey, colorIsCategory]);

  return (
    <Plot
      divId="chart-box"
      data={traces}
      layout={layout}
      config={baseConfig}
      style={{ width: '100%', height: '100%' }}
      useResizeHandler
    />
  );
}

/* ------------------------------------------------------------------
 *  ViolinPlot
 * ------------------------------------------------------------------ */
export function ViolinPlot({ rows, xKey, yKey, colorKey, xLabel, yLabel, colorIsCategory = true }) {
  const traces = useMemo(() => {
    const out = [];
    if (colorKey && colorIsCategory && colorKey !== xKey) {
      const colorGroups = groupBy(rows, colorKey);
      const ckeys = sortKeys(colorGroups.keys());
      let i = 0;
      for (const ck of ckeys) {
        const grp = colorGroups.get(ck);
        out.push({
          type: 'violin',
          x: grp.map((d) => d[xKey]),
          y: grp.map((d) => d[yKey]),
          name: String(ck),
          box: { visible: true },
          meanline: { visible: true },
          points: 'outliers',
          marker: { color: PALETTE[i % PALETTE.length], opacity: 0.8 },
          line: { color: PALETTE[i % PALETTE.length] },
          fillcolor: PALETTE[i % PALETTE.length] + '40',
        });
        i++;
      }
    } else {
      const xGroups = groupBy(rows, xKey);
      const xkeys = sortKeys(xGroups.keys());
      let i = 0;
      for (const xk of xkeys) {
        const grp = xGroups.get(xk);
        out.push({
          type: 'violin',
          x: grp.map(() => xk),
          y: grp.map((d) => d[yKey]),
          name: String(xk),
          box: { visible: true },
          meanline: { visible: true },
          points: 'outliers',
          marker: { color: PALETTE[i % PALETTE.length], opacity: 0.8 },
          line: { color: PALETTE[i % PALETTE.length] },
          fillcolor: PALETTE[i % PALETTE.length] + '40',
        });
        i++;
      }
    }
    return out;
  }, [rows, xKey, yKey, colorKey, colorIsCategory]);

  const layout = useMemo(() => ({
    ...baseLayout,
    showlegend: !!colorKey && colorIsCategory && colorKey !== xKey,
    violinmode: 'group',
    xaxis: { ...baseLayout.xaxis, title: xLabel || xKey, type: 'category' },
    yaxis: { ...baseLayout.yaxis, title: yLabel || yKey },
    legend: { orientation: 'h', y: 1.08, x: 0 },
  }), [xLabel, yLabel, xKey, yKey, colorKey, colorIsCategory]);

  return (
    <Plot
      divId="chart-violin"
      data={traces}
      layout={layout}
      config={baseConfig}
      style={{ width: '100%', height: '100%' }}
      useResizeHandler
    />
  );
}

/* ------------------------------------------------------------------
 *  LineChart — basic single/multi-series line.
 * ------------------------------------------------------------------ */
export function LineChart({ x, y, xLabel, yLabel, name, color, markers = [], series, showLegend, xIsCategory, extraShapes = [] }) {
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
        marker: { color: s.color || PALETTE[i % PALETTE.length], size: 5 },
        opacity: s.opacity != null ? s.opacity : 1,
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
  }, [series, x, y, name, color]);

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
      legend: { orientation: 'h', y: 1.08, x: 0 },
      xaxis: { ...baseLayout.xaxis, title: xLabel || 'x', type: xIsCategory ? 'category' : undefined },
      yaxis: { ...baseLayout.yaxis, title: yLabel || 'y' },
      shapes: [...markerShapes, ...extraShapes],
      annotations,
    };
  }, [xLabel, yLabel, showLegend, xIsCategory, markers, extraShapes]);

  return (
    <Plot
      divId="chart-line"
      data={traces}
      layout={layout}
      config={baseConfig}
      style={{ width: '100%', height: '100%' }}
      useResizeHandler
    />
  );
}

/* ------------------------------------------------------------------
 *  MultiLineChart
 *
 *  Build multiple line series from rows by:
 *    - groupKey (one line per group value)
 *    - xKey / yKey
 *  Within each group, points are sorted by X.
 * ------------------------------------------------------------------ */
export function MultiLineChart({ rows, xKey, yKey, colorKey, xLabel, yLabel, xIsCategory = false }) {
  const series = useMemo(() => {
    const groups = colorKey ? groupBy(rows, colorKey) : new Map([['all', rows]]);
    const gkeys = colorKey ? sortKeys(groups.keys()) : ['all'];
    return gkeys.map((gk, i) => {
      const grp = groups.get(gk);
      let sorted;
      if (xIsCategory) {
        sorted = grp.slice();
      } else {
        sorted = grp.slice().sort((a, b) => {
          const ax = a[xKey], bx = b[xKey];
          if (!isNum(ax)) return 1; if (!isNum(bx)) return -1;
          return ax - bx;
        });
      }
      return {
        name: colorKey ? String(gk) : '全部',
        x: sorted.map((d) => d[xKey]),
        y: sorted.map((d) => d[yKey]),
        color: PALETTE[i % PALETTE.length],
      };
    });
  }, [rows, xKey, yKey, colorKey, xIsCategory]);

  return (
    <LineChart
      series={series}
      xLabel={xLabel}
      yLabel={yLabel}
      showLegend={!!colorKey}
      xIsCategory={xIsCategory}
    />
  );
}

/* ------------------------------------------------------------------
 *  FacetedGrid
 *
 *  N rows × 1 column of small subplots, sharing the X axis. Each
 *  subplot shows a different yField from `yFields`. If `kind ==
 *  'violin'` and X is categorical, each subplot is a violin per X
 *  value (matching the customer reference shot). Otherwise scatter.
 *
 *  - rows: all data rows
 *  - xKey: X axis (typically categorical, e.g. 'eg')
 *  - yFields: array of {name, label, unit} for each row's Y
 *  - colorKey: optional sub-grouping color
 * ------------------------------------------------------------------ */
export function FacetedGrid({
  rows, xKey, yFields, colorKey,
  xLabel, xIsCategory = true, kind = 'violin',
  rowHeight = 110,
}) {
  if (!yFields || yFields.length === 0) {
    return <div style={{ padding: 20, color: 'var(--fg-4)' }}>请选择至少一个 Y 字段</div>;
  }

  const nRows = yFields.length;
  const traces = [];
  const layout = {
    ...baseLayout,
    showlegend: false,
    grid: { rows: nRows, columns: 1, pattern: 'independent', roworder: 'top to bottom' },
    margin: { l: 64, r: 16, t: 16, b: 40 },
    height: Math.max(nRows * rowHeight + 60, 360),
    annotations: [],
  };

  // Plotly subplot axes are indexed: xaxis, xaxis2, ...; yaxis, yaxis2, ...
  // With grid.pattern='independent', each row gets its own X+Y pair, but we
  // share visible ticks only on the bottom-most X for a clean look.

  const colorGroups = colorKey ? groupBy(rows, colorKey) : null;
  const colorKeys = colorGroups ? sortKeys(colorGroups.keys()) : null;

  yFields.forEach((yf, rIdx) => {
    const yKey = yf.name;
    const yLabel = yf.unit ? `${yf.label} (${yf.unit})` : yf.label || yf.name;
    const axisIdx = rIdx + 1;
    const xref = `x${axisIdx === 1 ? '' : axisIdx}`;
    const yref = `y${axisIdx === 1 ? '' : axisIdx}`;

    const showX = rIdx === nRows - 1;
    layout[`xaxis${axisIdx === 1 ? '' : axisIdx}`] = {
      ...baseLayout.xaxis,
      type: xIsCategory ? 'category' : undefined,
      title: showX ? xLabel || xKey : undefined,
      showticklabels: showX,
      tickfont: { size: 10 },
    };
    layout[`yaxis${axisIdx === 1 ? '' : axisIdx}`] = {
      ...baseLayout.yaxis,
      title: { text: yLabel, font: { size: 10 } },
      tickfont: { size: 9 },
      automargin: true,
    };

    const baseColor = PALETTE[rIdx % PALETTE.length];

    if (kind === 'violin') {
      // one violin per X value, optionally split by color
      if (colorGroups) {
        let ci = 0;
        for (const ck of colorKeys) {
          const grp = colorGroups.get(ck);
          traces.push({
            type: 'violin',
            x: grp.map((d) => d[xKey]),
            y: grp.map((d) => d[yKey]),
            xaxis: xref, yaxis: yref,
            name: `${ck}`,
            legendgroup: String(ck),
            showlegend: rIdx === 0,
            box: { visible: true }, meanline: { visible: true },
            points: false,
            marker: { color: PALETTE[ci % PALETTE.length] },
            line: { color: PALETTE[ci % PALETTE.length], width: 1 },
            fillcolor: PALETTE[ci % PALETTE.length] + '50',
            spanmode: 'soft',
          });
          ci++;
        }
      } else {
        traces.push({
          type: 'violin',
          x: rows.map((d) => d[xKey]),
          y: rows.map((d) => d[yKey]),
          xaxis: xref, yaxis: yref,
          name: yLabel,
          showlegend: false,
          box: { visible: true }, meanline: { visible: true },
          points: false,
          marker: { color: baseColor },
          line: { color: baseColor, width: 1 },
          fillcolor: baseColor + '50',
          spanmode: 'soft',
        });
      }
    } else {
      // scatter / strip per row
      if (colorGroups) {
        let ci = 0;
        for (const ck of colorKeys) {
          const grp = colorGroups.get(ck);
          traces.push({
            type: 'scatter', mode: 'markers',
            x: grp.map((d) => d[xKey]),
            y: grp.map((d) => d[yKey]),
            xaxis: xref, yaxis: yref,
            name: String(ck),
            legendgroup: String(ck),
            showlegend: rIdx === 0,
            marker: { color: PALETTE[ci % PALETTE.length], size: 5, opacity: 0.7 },
          });
          ci++;
        }
      } else {
        traces.push({
          type: 'scatter', mode: 'markers',
          x: rows.map((d) => d[xKey]),
          y: rows.map((d) => d[yKey]),
          xaxis: xref, yaxis: yref,
          name: yLabel, showlegend: false,
          marker: { color: baseColor, size: 5, opacity: 0.7 },
        });
      }
    }
  });

  if (colorGroups) {
    layout.showlegend = true;
    layout.legend = { orientation: 'h', y: 1.04, x: 0 };
  }

  return (
    <Plot
      divId="chart-faceted"
      data={traces}
      layout={layout}
      config={baseConfig}
      style={{ width: '100%', height: '100%' }}
      useResizeHandler
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
export function WaferMap({
  rows, valueField, valueLabel,
  facetField, facets,
  aggregated = false,
  onPointClick,
  onSelection,
}) {
  const { traces, layout } = useMemo(() => {
    const allXs = rows.map((r) => r['x']).filter(Number.isFinite);
    const allYs = rows.map((r) => r['y']).filter(Number.isFinite);
    const xMin = allXs.length ? Math.min(...allXs) - 1 : -1;
    const xMax = allXs.length ? Math.max(...allXs) + 1 : 1;
    const yMin = allYs.length ? Math.min(...allYs) - 1 : -1;
    const yMax = allYs.length ? Math.max(...allYs) + 1 : 1;

    const allVals = rows.map((r) => r[valueField]).filter(Number.isFinite);
    const vMin = allVals.length ? Math.min(...allVals) : 0;
    const vMax = allVals.length ? Math.max(...allVals) : 1;

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
          + '<extra></extra>'
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
          showscale: pIdx === panels.length - 1,
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
  }, [rows, valueField, valueLabel, facetField, facets, aggregated]);

  const handleClick = useCallback((e) => {
    if (onPointClick && e.points && e.points[0]) {
      onPointClick(e.points[0].customdata);
    }
  }, [onPointClick]);

  return (
    <Plot
      divId="chart-wafer"
      data={traces}
      layout={layout}
      config={baseConfig}
      style={{ width: '100%', height: '100%' }}
      useResizeHandler
      onClick={handleClick}
      onSelected={onSelection}
    />
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
function buildScatterTraces({ rows, xField, yField, zField, axisRef, useGl, zCategoryValues, showLegend }) {
  const xKey = xField.name;
  const yKey = yField.name;
  const xref = `x${axisRef}`;
  const yref = `y${axisRef}`;
  const xIsCat = !!xField.isCategorical;
  const yIsCat = !!yField.isCategorical;
  // scattergl does not work cleanly when either axis is categorical → use SVG.
  const wantGl = useGl && !xIsCat && !yIsCat;
  const traces = [];

  // When the X (or Y) axis is rendered as a category axis, stringify
  // the trace coordinates so they match the layout's string
  // categoryarray. Numeric axes keep their raw numeric values.
  const mapX = xIsCat ? (d) => String(d[xKey]) : (d) => d[xKey];
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
      const grp = rowsForZ(rows, zKey, zv);
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
function buildBoxTraces({ rows, xField, yField, zField, axisRef, zCategoryValues, showLegend, cellGroup }) {
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
        const grp = rowsForZ(rows, zKey, zv);
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
      });
    }
    return traces;
  }

  if (zField && zField.isCategorical) {
    const zKey = zField.name;
    const zVals = zCategoryValues || distinctSortedValues(rows, zKey, { includeNull: true });
    let i = 0;
    for (const zv of zVals) {
      const grp = rowsForZ(rows, zKey, zv);
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
    });
  }
  return traces;
}

// Violin plot for one cell. Y categorical → horizontal violin (one per Y).
//
// `cellGroup` is a unique-per-cell offsetgroup; see buildBoxTraces for why.
function buildViolinTraces({ rows, xField, yField, zField, axisRef, zCategoryValues, showLegend, cellGroup }) {
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
        const grp = rowsForZ(rows, zKey, zv);
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
      });
    }
    return traces;
  }

  if (zField && zField.isCategorical) {
    const zKey = zField.name;
    const zVals = zCategoryValues || distinctSortedValues(rows, zKey, { includeNull: true });
    let i = 0;
    for (const zv of zVals) {
      const grp = rowsForZ(rows, zKey, zv);
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
    });
  }
  return traces;
}

// Swarm (beeswarm) plot for one cell.
//
// Each X tick owns a horizontal "slot" of width 1 on the (forced) category
// axis. Within a slot we lay every sample out as a point: same Y as the
// data, but X is jittered DETERMINISTICALLY so points fan out by local
// density — visually similar to a violin's silhouette, but every dot is a
// real sample.
//
// Above SWARM_GL_THRESHOLD rows we render via scattergl (WebGL) instead of
// scatter (SVG) — pan/zoom/hover stops thrashing the DOM tree once the
// total marker count climbs into the tens of thousands. scattergl ignores
// most marker.line settings, so we drop the white outline in that mode
// (it would just render as fuzz anyway).
const SWARM_GL_THRESHOLD = 5000;
//
// Algorithm (histogram-binned beeswarm):
//   1. Bin Y values into K equal-width buckets across the global Y range.
//   2. Within each bucket, points are placed at offsets 0, +d, -d, +2d,
//      -2d, ... around the slot center.
//   3. d is chosen per "column" (X category × Z subgroup) so the densest
//      bucket fits inside that column's allowed width.
//
// On a category axis Plotly accepts numeric X values, interpreting them as
// fractional tick indices — that's how we can offset points off their
// tick without breaking the categorical axis.
//
// First version: only numeric Y. Categorical Y is reported via a warning
// so the caller can prompt the user to switch chart.
function buildSwarmTraces({ rows, xField, yField, zField, axisRef, zCategoryValues, showLegend, xCategoryArray }) {
  const xKey = xField.name;
  const yKey = yField.name;
  const xref = `x${axisRef}`;
  const yref = `y${axisRef}`;
  const yIsCat = !!yField.isCategorical;
  const traces = [];

  if (yIsCat) return traces;

  const useGl = rows.length >= SWARM_GL_THRESHOLD;
  const traceType = useGl ? 'scattergl' : 'scatter';
  const markerLine = useGl ? undefined : { width: 0.5, color: '#ffffff80' };

  const xCats = xCategoryArray || [];
  const xIndexOf = new Map(xCats.map((v, i) => [v, i]));

  const SLOT_PAD = 0.8;
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
  const swarmGroup = (group, cx, slotW) => {
    const xs = new Array(group.length);
    const ys = new Array(group.length);
    if (group.length === 0) return { xs, ys };
    const binIdxs = new Array(group.length);
    const binCounts = new Map();
    for (let i = 0; i < group.length; i++) {
      const v = group[i][yKey];
      if (!Number.isFinite(v)) { binIdxs[i] = -1; continue; }
      let b = Math.floor((v - yMin) / binWidth);
      if (b >= N_BINS) b = N_BINS - 1;
      if (b < 0) b = 0;
      binIdxs[i] = b;
      binCounts.set(b, (binCounts.get(b) || 0) + 1);
    }
    let maxBin = 1;
    for (const c of binCounts.values()) if (c > maxBin) maxBin = c;
    // Spread the densest bucket across the available width. Divide by
    // (maxBin - 1) so the outermost points sit on the slot edges; clamp
    // to avoid 1/0 for singleton buckets.
    const d = (maxBin > 1) ? (slotW * SLOT_PAD) / (maxBin - 1) : 0;
    const binProgress = new Map();
    for (let i = 0; i < group.length; i++) {
      ys[i] = group[i][yKey];
      const b = binIdxs[i];
      if (b < 0) { xs[i] = cx; continue; }
      const k = binProgress.get(b) || 0;
      binProgress.set(b, k + 1);
      const sign = (k % 2 === 0) ? 1 : -1;
      const mag = Math.ceil(k / 2);
      xs[i] = cx + sign * mag * d;
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

  if (hasZ) {
    const zVals = zCategoryValues || distinctSortedValues(rows, zKey, { includeNull: true });
    const nZ = zVals.length || 1;
    const subSlotW = 1 / nZ;
    let i = 0;
    for (const zv of zVals) {
      const c = PALETTE[i % PALETTE.length];
      const zCenter = -0.5 + (i + 0.5) * subSlotW;
      const allXs = [];
      const allYs = [];
      const allCustom = [];
      for (const xc of xCats) {
        const grp = groups.get(groupKey(xc, zv)) || [];
        const tickIdx = xIndexOf.get(xc);
        const { xs, ys } = swarmGroup(grp, tickIdx + zCenter, subSlotW);
        allXs.push(...xs);
        allYs.push(...ys);
        allCustom.push(...grp);
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
        marker: {
          size: 5,
          color: c,
          opacity: 0.78,
          line: markerLine,
        },
        customdata: allCustom,
        hovertemplate: `<b>${axisTitle(xField)}</b>=%{customdata.${xKey}}<br>${axisTitle(yField)}=%{y}<extra>${zv}</extra>`,
      });
      i++;
    }
  } else {
    const c = PALETTE[0];
    const allXs = [];
    const allYs = [];
    const allCustom = [];
    for (const xc of xCats) {
      const grp = groups.get(groupKey(xc, '__all__')) || [];
      const tickIdx = xIndexOf.get(xc);
      const { xs, ys } = swarmGroup(grp, tickIdx, 1);
      allXs.push(...xs);
      allYs.push(...ys);
      allCustom.push(...grp);
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
      marker: {
        size: 5,
        color: c,
        opacity: 0.78,
        line: markerLine,
      },
      customdata: allCustom,
      hovertemplate: `<b>${axisTitle(xField)}</b>=%{customdata.${xKey}}<br>${axisTitle(yField)}=%{y}<extra></extra>`,
    });
  }

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
function buildLineTraces({ rows, xField, yField, zField, axisRef, zCategoryValues, showLegend, xCategoryArray }) {
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
      const slot = acc.get(xv) || { sum: 0, n: 0 };
      slot.sum += yv;
      slot.n += 1;
      acc.set(xv, slot);
    }
    if (xIsCat) {
      const cats = xCategoryArray || [];
      const idx = (v) => {
        const i = cats.indexOf(String(v));
        return i < 0 ? Number.POSITIVE_INFINITY : i;
      };
      const out = [];
      for (const [xv, { sum, n }] of acc) out.push({ [xKey]: xv, [yKey]: sum / n });
      return out.sort((a, b) => idx(a[xKey]) - idx(b[xKey]));
    }
    const out = [];
    for (const [xv, { sum, n }] of acc) out.push({ [xKey]: xv, [yKey]: sum / n });
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
      const grp = prep(rowsForZ(rows, zKey, zv));
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

const PERF_GL_THRESHOLD = 50000;
// Hard ceiling on the number of distinct X categorical ticks per cell.
// Beyond this Plotly renders unusably (browser stalls, ticks overlap).
// We surface a warning and clamp the categoryarray to the head.
const X_CATEGORY_TICK_LIMIT = 80;

export function UnifiedChartGrid({
  chartType,
  rows,
  xFields,
  yFields,
  zField,
  height = 350,
  width = null,
  onPerformanceWarn,
  onWarn,
  onPointClick,
  onSelection,
}) {
  const cols = (xFields || []).length;
  const nRows = (yFields || []).length;
  const builder = BUILDER_MAP[chartType];
  const placeholder = cols === 0 || nRows === 0
    ? '请至少选一个 X / Y 字段'
    : !builder
      ? `未知图表类型：${String(chartType)}`
      : null;

  // Memoize the heavy trace+layout computation so it only re-runs when
  // the data or field configuration actually changes.
  const { allTraces, layout, pendingWarns, perfWarn } = useMemo(() => {
    const pw = [];
    const effZ = zField && Z_NUMERIC_UNSUPPORTED.has(chartType) && !zField.isCategorical
      ? null
      : zField || null;
    if (zField && Z_NUMERIC_UNSUPPORTED.has(chartType) && !zField.isCategorical) {
      pw.push({ kind: 'z_numeric_dropped', chartType, zField: zField.name });
    }

    const useGl = rows.length >= PERF_GL_THRESHOLD;
    const pWarn = rows.length >= PERF_GL_THRESHOLD
      ? { rowCount: rows.length, threshold: PERF_GL_THRESHOLD }
      : null;

    const zCategoryValues = effZ && effZ.isCategorical
      ? distinctSortedValues(rows, effZ.name, { includeNull: true })
      : null;

    const hasNumericZ = effZ && !effZ.isCategorical && chartType === 'scatter';
    const outTraces = [];
    const outLayout = {
      ...baseLayout,
      grid: {
        rows: nRows,
        columns: cols,
        pattern: 'independent',
        xgap: 0.08,
        ygap: 0.16,
        roworder: 'top to bottom',
      },
      margin: { l: 60, r: hasNumericZ ? 120 : 60, t: 60, b: 60 },
      height: nRows * height + 100,
      annotations: [],
      showlegend: !!effZ && effZ.isCategorical,
      legend: { orientation: 'h', y: 1.04, x: 0 },
      font: { ...baseLayout.font, size: 11 },
      boxmode: 'group',
      violinmode: 'group',
    };
    if (width) outLayout.width = width;

    const COLOR_OWNER_CELL = 1;

    if (!placeholder) for (let r = 0; r < nRows; r++) {
      const yField = yFields[r];
      for (let c = 0; c < cols; c++) {
        const xField = xFields[c];
        const cellIdx = r * cols + c + 1;
        const sfx = axisSuffix(cellIdx);
        const xAxisKey = `xaxis${sfx}`;
        const yAxisKey = `yaxis${sfx}`;

        const yIsCat = !!yField.isCategorical;

        const xLayout = {
          ...baseLayout.xaxis,
          title: { text: axisTitle(xField), font: { size: 11 } },
          tickfont: { size: 10 },
          automargin: true,
        };
        const xAsCategory = !!xField.isCategorical
          || ((chartType === 'box' || chartType === 'violin' || chartType === 'swarm') && !yIsCat);
        let xCategoryArray = null;
        if (xAsCategory) {
          const allCats = distinctSortedValues(rows, xField.name).map(String);
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
          if (chartType === 'swarm') {
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
        }

        outLayout[xAxisKey] = xLayout;

        const yLayout = {
          ...baseLayout.yaxis,
          title: { text: axisTitle(yField), font: { size: 11 } },
          tickfont: { size: 10 },
          automargin: true,
        };
        if (yIsCat) {
          const yCats = distinctSortedValues(rows, yField.name).map(String);
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
          rows,
          xField,
          yField,
          zField: effZ,
          axisRef: sfx,
          useGl,
          zCategoryValues,
          showLegend: cellIdx === COLOR_OWNER_CELL,
          cellGroup: `cell${cellIdx}`,
          xCategoryArray,
        });
        outTraces.push(...cellTraces);
      }
    }

    return { allTraces: outTraces, layout: outLayout, pendingWarns: pw, perfWarn: pWarn };
  }, [chartType, rows, xFields, yFields, zField, height, width, placeholder, cols, nRows, builder]);

  // Dispatch collected warnings AFTER render commits.
  const warnsKey = JSON.stringify(pendingWarns);
  useEffect(() => {
    if (!onWarn) return;
    for (const w of pendingWarns) onWarn(w);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [warnsKey, onWarn]);
  useEffect(() => {
    if (perfWarn && onPerformanceWarn) onPerformanceWarn(perfWarn);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [perfWarn?.rowCount, onPerformanceWarn]);

  const handleClick = useCallback((e) => {
    if (!onPointClick) return;
    const p = e.points && e.points[0];
    if (p && p.customdata) onPointClick(p.customdata);
  }, [onPointClick]);

  if (placeholder) {
    return (
      <div style={{ padding: 24, color: 'var(--fg-4)', fontSize: 12 }}>
        {placeholder}
      </div>
    );
  }

  return (
    <Plot
      divId="chart-unified"
      data={allTraces}
      layout={layout}
      config={baseConfig}
      style={{ width: '100%', height: layout.height }}
      useResizeHandler
      onClick={handleClick}
      onSelected={onSelection}
    />
  );
}

