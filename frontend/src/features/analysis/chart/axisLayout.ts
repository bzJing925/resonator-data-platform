import type { ChartField } from '../../../types';
import type { ChartHierarchy } from './types';

export const MISSING_BUCKET = '∅';

function displayBucket(value: unknown) {
  if (value === null || value === undefined || value === '') return MISSING_BUCKET;
  return typeof value === 'number' && Number.isFinite(value) ? value : String(value);
}

function naturalCompare(left: number | string, right: number | string) {
  if (typeof left === 'number' && typeof right === 'number') return left - right;
  return String(left).localeCompare(String(right), undefined, { numeric: true });
}

export function hierarchyKey(structureType: unknown, areaReality: unknown) {
  return `${JSON.stringify(String(displayBucket(structureType)))}:${JSON.stringify(displayBucket(areaReality))}`;
}

export function buildChartHierarchy(rows: Record<string, unknown>[]): ChartHierarchy {
  const areasByStructure = new Map<string, Set<number | string>>();
  const rowKeys: string[] = [];
  for (const row of rows) {
    const structure = String(displayBucket(row.structureType));
    const area = displayBucket(row.areaReality);
    rowKeys.push(hierarchyKey(structure, area));
    if (!areasByStructure.has(structure)) areasByStructure.set(structure, new Set());
    areasByStructure.get(structure)?.add(area);
  }

  const structures = [...areasByStructure.keys()].sort(naturalCompare);
  const slots: ChartHierarchy['slots'] = [];
  const groups: ChartHierarchy['structures'] = [];
  for (const structureType of structures) {
    const start = slots.length;
    const areas = [...(areasByStructure.get(structureType) || [])].sort(naturalCompare);
    for (const areaReality of areas) {
      slots.push({
        index: slots.length,
        structureType,
        areaReality,
        key: hierarchyKey(structureType, areaReality),
      });
    }
    groups.push({ name: structureType, start, end: slots.length - 1 });
  }

  const slotByKey = new Map(slots.map((slot) => [slot.key, slot]));
  const rowSlotIndices = new WeakMap<Record<string, unknown>, number>();
  rows.forEach((row, index) => {
    const slot = slotByKey.get(rowKeys[index]);
    if (slot) rowSlotIndices.set(row, slot.index);
  });

  return {
    slots,
    slotByKey,
    rowSlotIndices,
    structures: groups,
  };
}

export function slotForRow(hierarchy: ChartHierarchy, row: Record<string, unknown>) {
  const cachedIndex = hierarchy.rowSlotIndices.get(row);
  if (cachedIndex !== undefined) return hierarchy.slots[cachedIndex];
  return hierarchy.slotByKey.get(hierarchyKey(row.structureType, row.areaReality));
}

function axisSuffix(index: number) {
  return index === 0 ? '' : String(index + 1);
}

function axisTitle(field: ChartField) {
  const label = field.label || field.name;
  return field.unit ? `${label} (${field.unit})` : label;
}

const axisStyle = {
  gridcolor: 'rgba(95,168,211,0.10)',
  zeroline: false,
  automargin: true,
  tickfont: { color: '#8A919D', size: 10 },
};

export function buildAxisLayout(
  hierarchy: ChartHierarchy,
  yFields: ChartField[],
  datasetRevision: number | string | undefined,
  panelHeight: number,
  width: number | null,
) {
  const metricCount = yFields.length;
  const bottomAxis = `x${axisSuffix(Math.max(0, metricCount - 1))}`;
  const layout: any = {
    paper_bgcolor: '#181B21',
    plot_bgcolor: '#181B21',
    font: { family: 'Inter, system-ui, sans-serif', size: 11, color: '#C4C8D0' },
    margin: { l: 72, r: 28, t: 36, b: 136 },
    height: metricCount * panelHeight + 148,
    grid: {
      rows: metricCount,
      columns: 1,
      pattern: 'independent',
      roworder: 'top to bottom',
      ygap: Math.min(0.12, 0.3 / Math.max(1, metricCount)),
    },
    showlegend: false,
    boxmode: 'overlay',
    violinmode: 'overlay',
    hovermode: 'closest',
    dragmode: 'select',
    clickmode: 'event+select',
    annotations: [],
    meta: {
      analysisLayout: {
        outerX: 'structureType',
        innerX: 'areaReality',
        yMetrics: yFields.map((field) => field.name),
      },
      datasetRevision,
    },
  };
  if (width) layout.width = width;

  const tickvals = hierarchy.slots.map((slot) => slot.index);
  const ticktext = hierarchy.slots.map((slot) => String(slot.areaReality));
  const range = hierarchy.slots.length ? [-0.55, hierarchy.slots.length - 0.45] : [-0.5, 0.5];

  yFields.forEach((field, index) => {
    const suffix = axisSuffix(index);
    const xKey = `xaxis${suffix}`;
    const yKey = `yaxis${suffix}`;
    const isBottom = index === metricCount - 1;
    layout[xKey] = {
      ...axisStyle,
      type: 'linear',
      tickmode: 'array',
      tickvals,
      ticktext,
      range,
      matches: index === 0 ? undefined : 'x',
      showticklabels: isBottom,
      title: isBottom ? { text: '实际面积（Area_reality）', standoff: 12 } : undefined,
    };
    layout[yKey] = {
      ...axisStyle,
      title: { text: axisTitle(field), font: { size: 11 } },
    };
  });

  for (const group of hierarchy.structures) {
    const center = (group.start + group.end) / 2;
    layout.annotations.push({
      xref: bottomAxis,
      yref: 'paper',
      x: center,
      y: -0.1,
      yanchor: 'top',
      text: group.name,
      showarrow: false,
      font: { size: 11, color: '#C4C8D0' },
    });
    if (group.end < hierarchy.slots.length - 1) {
      layout.shapes = [...(layout.shapes || []), {
        type: 'line',
        xref: bottomAxis,
        yref: 'paper',
        x0: group.end + 0.5,
        x1: group.end + 0.5,
        y0: 0,
        y1: 1,
        line: { color: 'rgba(196,200,208,0.18)', width: 1 },
      }];
    }
  }
  return layout;
}

export function sceneAxisRefs(metricIndex: number) {
  const suffix = axisSuffix(metricIndex);
  return { xaxis: `x${suffix}`, yaxis: `y${suffix}` };
}
