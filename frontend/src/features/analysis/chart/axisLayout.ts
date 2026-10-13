import type { ChartField } from '../../../types';
import type { ChartHierarchy } from './types';

const MISSING_BUCKET = '∅';

function displayBucket(value: unknown) {
  if (value === null || value === undefined || value === '') return MISSING_BUCKET;
  return typeof value === 'number' && Number.isFinite(value) ? value : String(value);
}

function naturalCompare(left: number | string, right: number | string) {
  if (typeof left === 'number' && typeof right === 'number') return left - right;
  return String(left).localeCompare(String(right), undefined, { numeric: true });
}

function compareValueLists(left: Array<number | string>, right: Array<number | string>) {
  for (let index = 0; index < Math.max(left.length, right.length); index += 1) {
    if (left[index] === undefined) return -1;
    if (right[index] === undefined) return 1;
    const compared = naturalCompare(left[index], right[index]);
    if (compared !== 0) return compared;
  }
  return 0;
}

function valueKey(value: number | string) {
  return JSON.stringify([typeof value, value]);
}

function hierarchyKey(xValues: unknown[], zValue?: unknown) {
  const displayedX = xValues.map(displayBucket);
  const displayedZ = zValue === undefined ? undefined : displayBucket(zValue);
  return JSON.stringify({
    x: displayedX.map(valueKey),
    z: displayedZ === undefined ? null : valueKey(displayedZ),
  });
}

export function buildChartHierarchy(
  rows: Record<string, unknown>[],
  xFields: ChartField[],
  zField?: ChartField | null,
): ChartHierarchy {
  if (xFields.length === 0) {
    return {
      xFieldNames: [],
      zFieldName: zField?.name || null,
      slots: [],
      slotByKey: new Map(),
      rowSlotIndices: new WeakMap(),
      groups: [],
    };
  }

  const slotsByGroup = new Map<string, {
    label: string | null;
    value?: number | string;
    xValues: Map<string, Array<number | string>>;
  }>();
  const rowKeys: string[] = [];
  for (const row of rows) {
    const xValues = xFields.map((field) => displayBucket(row[field.name]));
    const zValue = zField ? displayBucket(row[zField.name]) : undefined;
    const groupKey = zValue === undefined ? '__all__' : valueKey(zValue);
    const slotKey = hierarchyKey(xValues, zValue);
    rowKeys.push(slotKey);
    if (!slotsByGroup.has(groupKey)) {
      slotsByGroup.set(groupKey, {
        label: zValue === undefined ? null : String(zValue),
        value: zValue,
        xValues: new Map(),
      });
    }
    slotsByGroup.get(groupKey)?.xValues.set(slotKey, xValues);
  }

  const orderedGroups = [...slotsByGroup.entries()].sort(([, left], [, right]) => {
    if (left.value === undefined) return -1;
    if (right.value === undefined) return 1;
    return naturalCompare(left.value, right.value);
  });
  const slots: ChartHierarchy['slots'] = [];
  const groups: ChartHierarchy['groups'] = [];
  for (const [groupKey, group] of orderedGroups) {
    const start = slots.length;
    const orderedXValues = [...group.xValues.entries()]
      .sort(([, left], [, right]) => compareValueLists(left, right));
    for (const [key, xValues] of orderedXValues) {
      slots.push({
        index: slots.length,
        xValues,
        label: xValues.map(String).join(' / '),
        zValue: group.value,
        key,
      });
    }
    groups.push({ key: groupKey, label: group.label, start, end: slots.length - 1 });
  }

  const slotByKey = new Map(slots.map((slot) => [slot.key, slot]));
  const rowSlotIndices = new WeakMap<Record<string, unknown>, number>();
  rows.forEach((row, index) => {
    const slot = slotByKey.get(rowKeys[index]);
    if (slot) rowSlotIndices.set(row, slot.index);
  });

  return {
    xFieldNames: xFields.map((field) => field.name),
    zFieldName: zField?.name || null,
    slots,
    slotByKey,
    rowSlotIndices,
    groups,
  };
}

export function slotForRow(hierarchy: ChartHierarchy, row: Record<string, unknown>) {
  const cachedIndex = hierarchy.rowSlotIndices.get(row);
  if (cachedIndex !== undefined) return hierarchy.slots[cachedIndex];
  const xValues = hierarchy.xFieldNames.map((name) => row[name]);
  const zValue = hierarchy.zFieldName ? row[hierarchy.zFieldName] : undefined;
  return hierarchy.slotByKey.get(hierarchyKey(xValues, zValue));
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

// 蜂群图每个档位的目标物理宽度：约 10 个点径（marker size 5px × 10）。
// 档数多时图宽度随档数增长，容器横向滚动（Explore 图表容器 overflow:auto）；
// 档数少时不设宽度，交给 plotly autosize 填满容器。ponytail: width 为空时
// 容器实际像素不可得，1100 是按桌面端典型图表区宽度的保守估计。
const SWARM_SLOT_MIN_PX = 50;
const SWARM_ASSUMED_CONTAINER_PX = 1100;

export function buildAxisLayout(
  hierarchy: ChartHierarchy,
  xFields: ChartField[],
  yFields: ChartField[],
  zField: ChartField | null | undefined,
  datasetRevision: number | string | undefined,
  panelHeight: number,
  width: number | null,
  chartType?: string,
) {
  const isSwarm = chartType === 'swarm';
  const metricCount = yFields.length;
  const bottomAxis = `x${axisSuffix(Math.max(0, metricCount - 1))}`;
  const layout: any = {
    paper_bgcolor: '#181B21',
    plot_bgcolor: '#181B21',
    font: { family: 'Inter, system-ui, sans-serif', size: 11, color: '#C4C8D0' },
    margin: { l: 72, r: 28, t: 36, b: zField ? 136 : 92 },
    height: metricCount * panelHeight + 148,
    grid: {
      rows: metricCount,
      columns: 1,
      pattern: 'independent',
      roworder: 'top to bottom',
      // 蜂群图要求子图纵向紧密堆叠、留白极小（间隙同时要容纳顶部中位数标注）
      ygap: isSwarm ? 0.03 : Math.min(0.12, 0.3 / Math.max(1, metricCount)),
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
        xFields: xFields.map((field) => field.name),
        zField: zField?.name || null,
        yMetrics: yFields.map((field) => field.name),
      },
      datasetRevision,
    },
  };
  if (width) layout.width = width;
  // 蜂群图：档位物理宽度不足 10 个点径时加宽整图（容器横向滚动）
  if (isSwarm) {
    const swarmMinWidth = hierarchy.slots.length * SWARM_SLOT_MIN_PX
      + layout.margin.l + layout.margin.r;
    if (swarmMinWidth > (width ?? SWARM_ASSUMED_CONTAINER_PX)) {
      layout.width = swarmMinWidth;
    }
  }

  const tickvals = hierarchy.slots.map((slot) => slot.index);
  const ticktext = hierarchy.slots.map((slot) => slot.label);
  const range = hierarchy.slots.length ? [-0.55, hierarchy.slots.length - 0.45] : [-0.5, 0.5];

  yFields.forEach((field, index) => {
    const suffix = axisSuffix(index);
    const xKey = `xaxis${suffix}`;
    const yKey = `yaxis${suffix}`;
    const isBottom = index === metricCount - 1;
    layout[xKey] = {
      ...axisStyle,
      // 蜂群图用档位间虚线分隔区间，刻度处的竖网格线会与之打架，关掉
      showgrid: !isSwarm,
      type: 'linear',
      tickmode: 'array',
      tickvals,
      ticktext,
      range,
      matches: index === 0 ? undefined : 'x',
      showticklabels: isBottom,
      title: isBottom ? {
        text: [zField ? axisTitle(zField) : null, ...xFields.map(axisTitle)].filter(Boolean).join(' / '),
        standoff: 12,
      } : undefined,
    };
    layout[yKey] = {
      ...axisStyle,
      title: { text: axisTitle(field), font: { size: 11 } },
    };
  });

  for (const group of zField ? hierarchy.groups : []) {
    const center = (group.start + group.end) / 2;
    layout.annotations.push({
      xref: bottomAxis,
      yref: 'paper',
      x: center,
      y: -0.1,
      yanchor: 'top',
      text: group.label,
      showarrow: false,
      font: { size: 11, color: '#C4C8D0' },
    });
    // 蜂群图改用档位间竖向实线框线（见下），组界线会与档位线重合，跳过
    if (!isSwarm && group.end < hierarchy.slots.length - 1) {
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
  // 蜂群图：相邻档位区间之间画竖向实线框线，贯穿所有子图
  if (isSwarm) {
    for (let boundary = 0; boundary < hierarchy.slots.length - 1; boundary += 1) {
      layout.shapes = [...(layout.shapes || []), {
        type: 'line',
        xref: bottomAxis,
        yref: 'paper',
        x0: boundary + 0.5,
        x1: boundary + 0.5,
        y0: 0,
        y1: 1,
        line: { color: 'rgba(196,200,208,0.28)', width: 1, dash: 'solid' },
      }];
    }
  }
  return layout;
}

export function sceneAxisRefs(metricIndex: number) {
  const suffix = axisSuffix(metricIndex);
  return { xaxis: `x${suffix}`, yaxis: `y${suffix}` };
}
