import { buildAxisLayout, buildChartHierarchy } from './axisLayout';
import { buildDistributionTraces } from './buildDistributionTraces';
import { buildRawPointTraces } from './buildRawPointTraces';
import { buildSummaryTraces } from './buildSummaryTraces';
import { buildSwarmMedianLayer } from './buildSwarmMedianTraces';
import type { AnalysisChartType, ChartScene, ChartSceneArgs } from './types';

const CHART_TYPES = new Set<AnalysisChartType>(['scatter', 'box', 'violin', 'swarm', 'line']);
const PERF_GL_THRESHOLD = 5000;

function isAnalysisChartType(value: string): value is AnalysisChartType {
  return CHART_TYPES.has(value as AnalysisChartType);
}

export function buildJmpChartScene({
  chartType,
  rows,
  xFields,
  yFields,
  zField,
  datasetRevision,
  showDataPoints = true,
  height = 250,
  width = null,
}: ChartSceneArgs): ChartScene {
  const placeholder = xFields.length === 0 || yFields.length === 0
    ? '请至少选一个 X 和 Y 字段'
    : !isAnalysisChartType(chartType) ? `未知图表类型：${chartType}` : null;
  const hierarchy = buildChartHierarchy(rows, xFields, zField);
  const layout = buildAxisLayout(hierarchy, xFields, yFields, zField, datasetRevision, height, width, chartType);
  if (placeholder) {
    return { allTraces: [], layout, pendingWarns: [], perfWarn: null, placeholder };
  }

  const effectiveChartType = chartType as AnalysisChartType;
  const raw = showDataPoints
    ? buildRawPointTraces({ chartType: effectiveChartType, rows, yFields, hierarchy })
    : [];
  let display: any[];
  if (effectiveChartType === 'box' || effectiveChartType === 'violin') {
    display = buildDistributionTraces({ kind: effectiveChartType, rows, yFields, hierarchy });
  } else if (effectiveChartType === 'line') {
    display = buildSummaryTraces({ rows, yFields, hierarchy });
  } else if (effectiveChartType === 'swarm') {
    const medianLayer = buildSwarmMedianLayer({ rows, yFields, hierarchy });
    display = medianLayer.traces;
    layout.annotations.push(...medianLayer.annotations);
  } else {
    display = [];
  }
  // 蜂群图的中位数折线压在散点之上，否则密集点云会把趋势线埋掉
  const orderedTraces = effectiveChartType === 'swarm' ? [...raw, ...display] : [...display, ...raw];
  const renderedPointCount = raw.reduce((sum, trace) => sum + trace.x.length, 0);
  const pointCount = raw.reduce(
    (sum, trace) => sum + (trace.meta.sourcePointCount || trace.x.length),
    0,
  );
  const perfWarn = pointCount >= PERF_GL_THRESHOLD
    ? { rowCount: rows.length, pointCount, renderedPointCount, threshold: PERF_GL_THRESHOLD }
    : null;

  return {
    allTraces: orderedTraces,
    layout,
    pendingWarns: [],
    perfWarn,
    placeholder: null,
  };
}
