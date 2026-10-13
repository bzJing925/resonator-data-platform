import type { ChartField } from '../../../types';
import { sceneAxisRefs, slotForRow } from './axisLayout';
import { stableTraceUid, type AnalysisChartType, type ChartHierarchy } from './types';

const RAW_COLORS = ['#5FA8D3', '#4BD097', '#E8C547', '#9B7EDC', '#E86B5C'];
const GL_POINT_THRESHOLD = 5000;
const RAW_POINT_RENDER_LIMIT = 50_000;

// 蜂群图配色：每个指标子图一种高饱和纯色（蓝、红、绿、紫、橙、青绿、品红、
// 橄榄黄），median 为同色系更深的中位数折线色；超过 8 个指标时循环复用。
export const SWARM_COLORS = [
  { point: '#2E90FF', median: '#0F5CC4' },
  { point: '#F24949', median: '#B01818' },
  { point: '#35C759', median: '#177A32' },
  { point: '#9D4EDD', median: '#6525A8' },
  { point: '#FF8A1E', median: '#B85A00' },
  { point: '#14B8A6', median: '#0A7A6E' },
  { point: '#EC3E9C', median: '#A81260' },
  { point: '#A8B120', median: '#6E7510' },
];

// 蜂群纵向分箱数 ≈ 子图高度(250px) / 点径(5px)：同箱内的点横向堆叠，
// 箱内点数越多蜂群越宽，形成类似小提琴的密度轮廓。横向步长全指标统一，
// 保证不同档位间的宽度可直接比较。档位目标宽度约 10 个点径（见
// axisLayout 的 SWARM_SLOT_MIN_PX）：步长 0.1 ≈ 一个点径，蜂群最宽
// ±0.45 ≈ 9 个点径，铺满档位但不越界（边界由实线框线标出）。
const SWARM_Y_BINS = 40;
const SWARM_BASE_STEP = 0.1;
const SWARM_MAX_HALF_WIDTH = 0.45;

type SwarmPoint = { slotIndex: number; offset: number; y: number; rowId: string };

function assignSwarmOffsets(series: SwarmPoint[][]) {
  const allBins: SwarmPoint[][] = [];
  for (const points of series) {
    if (points.length === 0) continue;
    let yMin = Infinity;
    let yMax = -Infinity;
    for (const point of points) {
      if (point.y < yMin) yMin = point.y;
      if (point.y > yMax) yMax = point.y;
    }
    const binSize = (yMax - yMin) / SWARM_Y_BINS || 1;
    const bins = new Map<string, typeof points>();
    for (const point of points) {
      const key = `${point.slotIndex}:${Math.floor((point.y - yMin) / binSize)}`;
      const bin = bins.get(key);
      if (bin) bin.push(point);
      else bins.set(key, [point]);
    }
    allBins.push(...bins.values());
  }
  let widest = 1;
  for (const bin of allBins) widest = Math.max(widest, bin.length);
  const step = Math.min(SWARM_BASE_STEP, (SWARM_MAX_HALF_WIDTH * 2) / widest);
  for (const bin of allBins) {
    bin.sort((left, right) => left.rowId.localeCompare(right.rowId, undefined, { numeric: true }));
    bin.forEach((point, index) => {
      point.offset = (index - (bin.length - 1) / 2) * step;
    });
  }
}

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

function rawOffset(rowId: string, chartType: AnalysisChartType) {
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
  const datasetIds = new Map(rows.map((row) => [rowIdOf(row), row.datasetId]));
  const datasetRef = (id: string) => typeof datasetIds.get(id) === 'string' ? { datasetId: datasetIds.get(id) } : {};
  const swarmSeries: SwarmPoint[][] = [];
  const traces = yFields.map((field, metricIndex) => {
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
      slotIndex: number;
      offset: number;
      y: number;
      rowId: string;
      memberRowIds?: string[];
    }> = [];
    for (const [slotIndex, members] of grouped) {
      if (chartType === 'swarm') {
        members.sort((left, right) => left.rowId.localeCompare(right.rowId, undefined, { numeric: true }));
      }
      for (let memberIndex = 0; memberIndex < members.length; memberIndex += bucketSize) {
        const bucket = members.slice(memberIndex, memberIndex + bucketSize);
        const member = bucket[Math.floor(bucket.length / 2)];
        points.push({
          slotIndex,
          offset: chartType === 'swarm' ? 0 : rawOffset(member.rowId, chartType),
          y: member.y,
          rowId: member.rowId,
          memberRowIds: bucket.length > 1 ? bucket.map((item) => item.rowId) : undefined,
        });
      }
    }
    if (chartType === 'swarm') swarmSeries.push(points);

    const traceKey = `analysis:${chartType}:${field.name}:raw`;
    const overlay = chartType === 'box' || chartType === 'violin' || chartType === 'line';
    const pointStyleDefaults = chartType === 'swarm'
      ? {
          color: SWARM_COLORS[metricIndex % SWARM_COLORS.length].point,
          size: 5,
          symbol: 'circle',
          // 不透明：交叠处不叠加加深，全图颜色保持一致
          opacity: 1,
          outlineColor: 'rgba(0,0,0,0)',
          outlineWidth: 0,
        }
      : {
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
      x: points.map((point) => point.slotIndex + point.offset),
      y: points.map((point) => point.y),
      customdata: points.map((point) => point.memberRowIds
        ? {
            ...datasetRef(point.rowId),
            rowId: point.rowId,
            __memberRowIds: point.memberRowIds,
            __memberCount: point.memberRowIds.length,
            memberCount: point.memberRowIds.length,
          }
        : { ...datasetRef(point.rowId), rowId: point.rowId, ...(bucketSize > 1 ? { memberCount: 1 } : {}) }),
      marker: {
        color: pointStyleDefaults.color,
        size: pointStyleDefaults.size,
        opacity: pointStyleDefaults.opacity,
        line: {
          color: pointStyleDefaults.outlineColor,
          width: pointStyleDefaults.outlineWidth,
        },
      },
      selected: { marker: { opacity: 1, size: overlay || chartType === 'swarm' ? 7 : 8 } },
      unselected: { marker: { opacity: 0.18 } },
      hovertemplate: `<b>${field.label || field.name}</b>: %{y}<br>rowId: %{customdata.rowId}`
        + (bucketSize > 1 ? '<br>映射行数: %{customdata.memberCount}' : '')
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
  if (chartType === 'swarm') {
    assignSwarmOffsets(swarmSeries);
    traces.forEach((trace, index) => {
      trace.x = swarmSeries[index].map((point) => point.slotIndex + point.offset);
    });
  }
  // SVG cost grows with the whole figure, even when each metric is small.
  const renderedPointCount = traces.reduce((sum, trace) => sum + trace.x.length, 0);
  if (renderedPointCount >= GL_POINT_THRESHOLD) {
    traces.forEach((trace) => { trace.type = 'scattergl'; });
  }
  return traces;
}
