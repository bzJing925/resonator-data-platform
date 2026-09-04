import type { ChartField } from '../../../types';
import { sceneAxisRefs, slotForRow } from './axisLayout';
import { stableTraceUid, type ChartHierarchy } from './types';

const DISTRIBUTION_COLORS = ['#5FA8D3', '#4BD097', '#E8C547', '#9B7EDC', '#E86B5C'];

export function buildDistributionTraces({
  kind,
  rows,
  yFields,
  hierarchy,
}: {
  kind: 'box' | 'violin';
  rows: Record<string, unknown>[];
  yFields: ChartField[];
  hierarchy: ChartHierarchy;
}) {
  return yFields.map((field, metricIndex) => {
    const points = rows.flatMap((row) => {
      const slot = slotForRow(hierarchy, row);
      const y = row[field.name];
      return slot && typeof y === 'number' && Number.isFinite(y)
        ? [{ x: slot.index, y }]
        : [];
    });
    const traceKey = `analysis:${kind}:${field.name}:distribution`;
    const color = DISTRIBUTION_COLORS[metricIndex % DISTRIBUTION_COLORS.length];
    return {
      type: kind,
      ...sceneAxisRefs(metricIndex),
      uid: stableTraceUid(traceKey),
      name: field.label || field.name,
      showlegend: false,
      x: points.map((point) => point.x),
      y: points.map((point) => point.y),
      boxpoints: kind === 'box' ? 'outliers' : undefined,
      points: kind === 'violin' ? false : undefined,
      width: 0.62,
      fillcolor: `${color}30`,
      line: { color, width: 1.4 },
      meanline: kind === 'violin' ? { visible: true } : undefined,
      hoveron: kind === 'violin' ? 'violins' : 'boxes',
      meta: { analysisLayer: 'distribution', traceKey, metric: field.name },
    };
  });
}
