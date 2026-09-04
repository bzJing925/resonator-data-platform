import type { AnalysisDataset, RowId } from './model/dataset';
import { datumRowIds } from './chart/chartInteraction';
import {
  pointStyleMetricKey,
  type PointStyleOverride,
  type PointStylePatch,
} from './model/commands';

export type PointStyleScope = 'selected' | 'group' | 'series' | 'metric';

export interface PointStyleTarget {
  rowIds: RowId[];
  metric?: string;
}

export function resolvePointStyleTarget({
  dataset,
  selectedRowIds,
  anchorRowId,
  scope,
  metric,
}: {
  dataset: AnalysisDataset;
  selectedRowIds: Set<RowId>;
  anchorRowId: RowId | null;
  scope: PointStyleScope;
  metric: string;
}): PointStyleTarget {
  if (scope === 'metric') return metric ? { rowIds: [], metric } : { rowIds: [] };
  if (scope === 'selected') return { rowIds: [...selectedRowIds] };

  const anchor = dataset.rows.find((row) => row.rowId === anchorRowId)
    || dataset.rows.find((row) => selectedRowIds.has(row.rowId));
  if (!anchor) return { rowIds: [] };
  return {
    rowIds: dataset.rows
      .filter((row) => row.structureType === anchor.structureType
        && (scope === 'series' || row.areaReality === anchor.areaReality))
      .map((row) => row.rowId),
  };
}

export function pointStyleForTarget(
  styles: Record<string, PointStyleOverride> | undefined,
  target: PointStyleTarget,
) {
  if (!styles) return {};
  const key = target.metric ? pointStyleMetricKey(target.metric) : target.rowIds[0];
  return key ? styles[key] || {} : {};
}

export function resolvedPointStyle(
  styles: Record<string, PointStyleOverride> | undefined,
  rowId: string,
  metric: string,
) {
  return {
    ...(styles?.[pointStyleMetricKey(metric)] || {}),
    ...(styles?.[rowId] || {}),
  };
}

function valueAt(value: unknown, index: number, fallback: unknown) {
  if (Array.isArray(value)) return value[index] ?? fallback;
  return value ?? fallback;
}

export interface PointStyleRestyle {
  traceIndex: number;
  update: Record<string, unknown>;
}

export function buildPointStyleRestyles(
  traces: any[],
  styles: Record<string, PointStyleOverride> | undefined,
): PointStyleRestyle[] {
  const updates: PointStyleRestyle[] = [];
  traces.forEach((trace, traceIndex) => {
    if (trace.meta?.analysisLayer !== 'raw-points') return;
    const metric = String(trace.meta?.metric || '');
    const marker = trace.marker || {};
    const line = marker.line || {};
    const defaults = trace.meta?.pointStyleDefaults || {};
    const customdata = Array.isArray(trace.customdata) ? trace.customdata : [];
    const resolved = customdata.map((datum, pointIndex) => {
      const rowIds = datumRowIds(datum);
      const rowId = rowIds.find((candidate) => styles?.[candidate]) || rowIds[0] || '';
      const override = resolvedPointStyle(styles, rowId, metric);
      return {
        color: override.color
          ?? defaults.color ?? valueAt(marker.color, pointIndex, '#5FA8D3'),
        size: override.size ?? defaults.size ?? valueAt(marker.size, pointIndex, 6),
        symbol: override.symbol
          ?? defaults.symbol ?? valueAt(marker.symbol, pointIndex, 'circle'),
        opacity: override.opacity
          ?? defaults.opacity ?? valueAt(marker.opacity, pointIndex, 1),
        outlineColor: override.outlineColor
          ?? defaults.outlineColor
          ?? valueAt(line.color, pointIndex, 'rgba(255,255,255,0.35)'),
        outlineWidth: override.outlineWidth
          ?? defaults.outlineWidth ?? valueAt(line.width, pointIndex, 0.5),
      };
    });
    updates.push({
      traceIndex,
      update: {
        'marker.color': [resolved.map((style) => style.color)],
        'marker.size': [resolved.map((style) => style.size)],
        'marker.symbol': [resolved.map((style) => style.symbol)],
        'marker.opacity': [resolved.map((style) => style.opacity)],
        'marker.line.color': [resolved.map((style) => style.outlineColor)],
        'marker.line.width': [resolved.map((style) => style.outlineWidth)],
      },
    });
  });
  return updates;
}

export const RESET_POINT_STYLE: PointStylePatch = {
  color: null,
  size: null,
  symbol: null,
  opacity: null,
  outlineColor: null,
  outlineWidth: null,
};
