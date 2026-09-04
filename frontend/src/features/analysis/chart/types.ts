import type { ChartField } from '../../../types';

export type AnalysisChartType = 'scatter' | 'box' | 'violin' | 'swarm' | 'line';

export interface ChartSceneArgs {
  chartType: string;
  rows: Record<string, unknown>[];
  yFields: ChartField[];
  datasetRevision?: number | string;
  height?: number;
  width?: number | null;
}

export interface ChartSlot {
  index: number;
  structureType: string;
  areaReality: number | string;
  key: string;
}

export interface ChartHierarchy {
  slots: ChartSlot[];
  slotByKey: Map<string, ChartSlot>;
  rowSlotIndices: WeakMap<Record<string, unknown>, number>;
  structures: Array<{
    name: string;
    start: number;
    end: number;
  }>;
}

export interface SceneTraceMeta {
  analysisLayer: 'raw-points' | 'distribution' | 'summary';
  traceKey: string;
  metric: string;
}

export interface ChartScene {
  allTraces: any[];
  layout: any;
  pendingWarns: any[];
  perfWarn: {
    rowCount: number;
    pointCount: number;
    renderedPointCount?: number;
    threshold: number;
  } | null;
  placeholder: string | null;
}

export function stableTraceUid(traceKey: string) {
  let hash = 2166136261;
  for (let index = 0; index < traceKey.length; index += 1) {
    hash ^= traceKey.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  const readable = traceKey.replace(/[^a-zA-Z0-9_-]+/g, '-').slice(0, 48);
  return `trace-${readable}-${(hash >>> 0).toString(36)}`;
}
