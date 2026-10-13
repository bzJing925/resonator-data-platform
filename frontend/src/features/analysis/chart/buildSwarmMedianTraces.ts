import type { ChartField } from '../../../types';
import { sceneAxisRefs, slotForRow } from './axisLayout';
import { SWARM_COLORS } from './buildRawPointTraces';
import { stableTraceUid, type ChartHierarchy } from './types';

export function medianOf(values: number[]) {
  const sorted = [...values].sort((left, right) => left - right);
  const middle = sorted.length >> 1;
  return sorted.length % 2 === 1
    ? sorted[middle]
    : (sorted[middle - 1] + sorted[middle]) / 2;
}

// 中位数标注的紧凑格式：fs/Zs(~1)、k2eff(~5)、Qs(~1000) 等常见量级都落在定点表示内。
export function formatMedianLabel(value: number) {
  const abs = Math.abs(value);
  if (abs >= 1000) return value.toFixed(0);
  if (abs >= 100) return value.toFixed(1);
  if (abs >= 1) return value.toFixed(2);
  return value.toPrecision(2);
}

// 档位过多时横向标注必然重叠：改为竖排并收进子图顶边内侧
const MEDIAN_LABEL_ROTATE_THRESHOLD = 40;

// 蜂群图中位数图层：每个指标一条贯穿整个子图的同色系深色折线连接各档位
// 中位数，并把中位数值标注在子图顶部对应 x 位置上方。
export function buildSwarmMedianLayer({
  rows,
  yFields,
  hierarchy,
}: {
  rows: Record<string, unknown>[];
  yFields: ChartField[];
  hierarchy: ChartHierarchy;
}) {
  const traces: any[] = [];
  const annotations: any[] = [];
  const denseSlots = hierarchy.slots.length > MEDIAN_LABEL_ROTATE_THRESHOLD;
  yFields.forEach((field, metricIndex) => {
    const valuesBySlot = new Map<number, number[]>();
    for (const row of rows) {
      const slot = slotForRow(hierarchy, row);
      const y = row[field.name];
      if (!slot || typeof y !== 'number' || !Number.isFinite(y)) continue;
      const values = valuesBySlot.get(slot.index);
      if (values) values.push(y);
      else valuesBySlot.set(slot.index, [y]);
    }
    const medians = hierarchy.slots.map((slot) => {
      const values = valuesBySlot.get(slot.index);
      return values?.length
        ? { x: slot.index, y: medianOf(values), count: values.length }
        : null;
    });
    const palette = SWARM_COLORS[metricIndex % SWARM_COLORS.length];
    const refs = sceneAxisRefs(metricIndex);
    const traceKey = `analysis:swarm:${field.name}:median`;
    const x = hierarchy.slots.map((slot) => slot.index);
    const y = medians.map((median) => (median ? median.y : null));
    // 亮描边（halo）衬底：深色趋势线压在半透明点云上时，靠描边把线从点云里分离出来
    traces.push({
      type: 'scatter',
      mode: 'lines',
      ...refs,
      uid: stableTraceUid(`${traceKey}:halo`),
      name: `${field.label || field.name} 中位数描边`,
      showlegend: false,
      x,
      y,
      connectgaps: true,
      line: { color: 'rgba(232,235,240,0.55)', width: 4.5 },
      hoverinfo: 'skip',
      meta: { analysisLayer: 'swarm-median', traceKey: `${traceKey}:halo`, metric: field.name },
    });
    traces.push({
      type: 'scatter',
      mode: 'lines+markers',
      ...refs,
      uid: stableTraceUid(traceKey),
      name: `${field.label || field.name} 中位数`,
      showlegend: false,
      x,
      y,
      customdata: medians.map((median) => ({ count: median?.count ?? 0 })),
      connectgaps: true,
      line: { color: palette.median, width: 2.5 },
      marker: { color: palette.median, size: 5, symbol: 'circle' },
      hovertemplate: `<b>${field.label || field.name} 中位数</b>: %{y}<br>n=%{customdata.count}<extra></extra>`,
      meta: { analysisLayer: 'swarm-median', traceKey, metric: field.name },
    });
    for (const median of medians) {
      if (!median) continue;
      annotations.push({
        xref: refs.xaxis,
        yref: `${refs.yaxis} domain`,
        x: median.x,
        y: denseSlots ? 0.995 : 1,
        yanchor: denseSlots ? 'top' : 'bottom',
        textangle: denseSlots ? -90 : 0,
        text: formatMedianLabel(median.y),
        showarrow: false,
        font: { size: 9, color: palette.point },
        bgcolor: 'rgba(24,27,33,0.65)',
        borderpad: 1,
      });
    }
  });
  return { traces, annotations };
}
