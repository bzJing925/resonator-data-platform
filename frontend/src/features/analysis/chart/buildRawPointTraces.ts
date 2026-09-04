import type { ChartField } from '../../../types';
import { sceneAxisRefs, slotForRow } from './axisLayout';
import { stableTraceUid, type AnalysisChartType, type ChartHierarchy } from './types';

const RAW_COLORS = ['#5FA8D3', '#4BD097', '#E8C547', '#9B7EDC', '#E86B5C'];
const GL_POINT_THRESHOLD = 5000;
export const RAW_POINT_RENDER_LIMIT = 50_000;

function numericValue(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function rowIdOf(row: Record<string, unknown>) {
  const value = row.rowId ?? row.__rowId;
  return typeof value === 'string' && value ? value : null;
}

function stableFraction(value: string) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) / 0xffffffff;
}

function rawOffset(rowId: string, chartType: AnalysisChartType, index: number, count: number) {
  if (chartType === 'swarm') {
    if (count <= 1) return 0;
    return ((index / (count - 1)) - 0.5) * 0.52;
  }
  const spread = chartType === 'scatter' ? 0.48 : 0.3;
  return (stableFraction(rowId) - 0.5) * spread;
}

export function buildRawPointTraces({
  chartType,
  rows,
  yFields,
  hierarchy,
}: {
  chartType: AnalysisChartType;
  rows: Record<string, unknown>[];
  yFields: ChartField[];
  hierarchy: ChartHierarchy;
}) {
  return yFields.map((field, metricIndex) => {
    const grouped = new Map<number, Array<{ rowId: string; y: number }>>();
    for (const row of rows) {
      const rowId = rowIdOf(row);
      const y = numericValue(row[field.name]);
      const slot = slotForRow(hierarchy, row);
      if (!rowId || y === null || !slot) continue;
      if (!grouped.has(slot.index)) grouped.set(slot.index, []);
      grouped.get(slot.index)?.push({ rowId, y });
    }

    const sourcePointCount = [...grouped.values()].reduce((sum, members) => sum + members.length, 0);
    const bucketSize = Math.max(1, Math.ceil(sourcePointCount / RAW_POINT_RENDER_LIMIT));
    const points: Array<{
      x: number;
      y: number;
      rowId: string;
      memberRowIds?: string[];
    }> = [];
    for (const [slotIndex, members] of grouped) {
      if (chartType === 'swarm') {
        members.sort((left, right) => left.rowId.localeCompare(right.rowId, undefined, { numeric: true }));
      }
      const displayedCount = Math.ceil(members.length / bucketSize);
      for (let memberIndex = 0, pointIndex = 0;
        memberIndex < members.length;
        memberIndex += bucketSize, pointIndex += 1) {
        const bucket = members.slice(memberIndex, memberIndex + bucketSize);
        const member = bucket[Math.floor(bucket.length / 2)];
        points.push({
          x: slotIndex + rawOffset(member.rowId, chartType, pointIndex, displayedCount),
          y: member.y,
          rowId: member.rowId,
          memberRowIds: bucket.length > 1 ? bucket.map((item) => item.rowId) : undefined,
        });
      }
    }

    const traceKey = `analysis:${chartType}:${field.name}:raw`;
    const overlay = chartType === 'box' || chartType === 'violin' || chartType === 'line';
    const pointStyleDefaults = {
      color: RAW_COLORS[metricIndex % RAW_COLORS.length],
      size: overlay ? 5 : 6,
      symbol: 'circle',
      opacity: overlay ? 0.52 : 0.82,
      outlineColor: 'rgba(255,255,255,0.35)',
      outlineWidth: 0.5,
    };
    return {
      type: points.length >= GL_POINT_THRESHOLD ? 'scattergl' : 'scatter',
      mode: 'markers',
      ...sceneAxisRefs(metricIndex),
      uid: stableTraceUid(traceKey),
      name: field.label || field.name,
      showlegend: false,
      x: points.map((point) => point.x),
      y: points.map((point) => point.y),
      customdata: points.map((point) => point.memberRowIds
        ? {
            rowId: point.rowId,
            __memberRowIds: point.memberRowIds,
            __memberCount: point.memberRowIds.length,
          }
        : { rowId: point.rowId }),
      marker: {
        color: pointStyleDefaults.color,
        size: pointStyleDefaults.size,
        opacity: pointStyleDefaults.opacity,
        line: {
          color: pointStyleDefaults.outlineColor,
          width: pointStyleDefaults.outlineWidth,
        },
      },
      selected: { marker: { opacity: 1, size: overlay ? 7 : 8 } },
      unselected: { marker: { opacity: 0.18 } },
      hovertemplate: `<b>${field.label || field.name}</b>: %{y}<br>rowId: %{customdata.rowId}`
        + (bucketSize > 1 ? '<br>映射行数: %{customdata.__memberCount}' : '')
        + '<extra></extra>',
      meta: {
        analysisLayer: 'raw-points',
        traceKey,
        metric: field.name,
        sourcePointCount,
        renderedPointCount: points.length,
        pointStyleDefaults,
      },
    };
  });
}
