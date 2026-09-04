import type { ChartField } from '../../../types';
import { sceneAxisRefs, slotForRow } from './axisLayout';
import { stableTraceUid, type ChartHierarchy } from './types';

const SUMMARY_COLORS = ['#B8DBEF', '#9BE8C7', '#F3DC78', '#CABAF0', '#F3A49A'];

export function buildSummaryTraces({
  rows,
  yFields,
  hierarchy,
}: {
  rows: Record<string, unknown>[];
  yFields: ChartField[];
  hierarchy: ChartHierarchy;
}) {
  return yFields.flatMap((field, metricIndex) => {
    const values = new Map<number, number[]>();
    for (const row of rows) {
      const slot = slotForRow(hierarchy, row);
      const y = row[field.name];
      if (!slot || typeof y !== 'number' || !Number.isFinite(y)) continue;
      if (!values.has(slot.index)) values.set(slot.index, []);
      values.get(slot.index)?.push(y);
    }
    const color = SUMMARY_COLORS[metricIndex % SUMMARY_COLORS.length];
    return hierarchy.structures.map((structure) => {
      const summaries = [...values.entries()]
        .filter(([x]) => x >= structure.start && x <= structure.end)
        .sort(([left], [right]) => left - right)
        .map(([x, ys]) => ({
          x,
          y: ys.reduce((sum, value) => sum + value, 0) / ys.length,
          count: ys.length,
        }));
      const traceKey = `analysis:line:${field.name}:summary:${structure.name}`;
      return {
        type: 'scatter',
        mode: 'lines+markers',
        ...sceneAxisRefs(metricIndex),
        uid: stableTraceUid(traceKey),
        name: `${field.label || field.name} 均值 · ${structure.name}`,
        showlegend: false,
        x: summaries.map((summary) => summary.x),
        y: summaries.map((summary) => summary.y),
        customdata: summaries.map((summary) => ({ count: summary.count })),
        line: { color, width: 2 },
        marker: { color, size: 7, symbol: 'diamond' },
        hovertemplate: `<b>${field.label || field.name} 均值</b>: %{y}<br>n=%{customdata.count}<extra></extra>`,
        meta: { analysisLayer: 'summary', traceKey, metric: field.name },
      };
    });
  });
}
