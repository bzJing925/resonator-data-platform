import type { ProjectVersionWithRows } from '../project/projectTypes';
import { validateProjectBackup } from '../project/projectCodec';
import { buildJmpChartScene } from '../chart/chartScene';
import type { ChartScene } from '../chart/types';
import { buildWaferSpec } from '../../../components/Charts';
import { comparisonColorDomain, comparisonWaferCells, differenceRows, distributionScene } from '../comparison/comparisonScene';
import { summarizeCohort } from '../comparison/comparisonData';
import { computeMetricSummary, summarizeQuality } from '../comparison/comparisonStatistics';
import { unverifiedManifest } from '../result/resultManifest';
export interface ReportFigure { title: string; scene: ChartScene }
export function freezeReportInput(project: ProjectVersionWithRows, notes = project.version.notes) {
  const generatedAt = new Date().toISOString();
  const copy = validateProjectBackup({ format: 'aln-analysis-project', schema_version: 1, exported_at: generatedAt, project: structuredClone(project) }).project;
  const spec = copy.version.spec;
  const datasets = copy.version.registryMetadata.order.map((id) => copy.datasets[id]);
  const summaries = datasets.map((dataset) => ({ id: dataset.id, name: dataset.name,
    manifest: { ...(dataset.manifest ?? unverifiedManifest(spec.query_spec, dataset.rows.length)), returned: dataset.rows.length, local_edits: dataset.edits.length, excluded_rows: dataset.exclusions.length },
    ...summarizeQuality(dataset.rows, spec.quality_rule),
    statistics: Object.fromEntries([...new Set([...spec.chart.y_fields.map((field) => field.name), ...(spec.comparison?.metrics ?? [])])].map((metric) => [metric, computeMetricSummary(dataset.rows, metric)])) }));
  const figures: ReportFigure[] = [];
  const field = (name: string) => ({ name, label: name, unit: spec.units[name] ?? '', isCategorical: ['wafer', 's_param_port', 'structureType', 'batch_no'].includes(name) });
  const wafer = (dataset: typeof datasets[number], metric: string, domain?: [number, number] | null) => {
    const cells = comparisonWaferCells(dataset, metric);
    const built = buildWaferSpec({ rows: cells, valueField: metric, valueLabel: `${metric} (${spec.units[metric] ?? '未知单位'}) · 均值`, facetField: 'wafer', facets: [...new Set(cells.map((cell) => cell.wafer))], aggregated: true, colorDomain: domain });
    return { allTraces: built.traces, layout: built.layout, pendingWarns: [], perfWarn: null, placeholder: null };
  };
  const chartDatasetId = copy.version.registryMetadata.chartPages[copy.version.activeTabId]?.datasetId ?? (copy.datasets[copy.version.activeTabId] ? copy.version.activeTabId : copy.version.registryMetadata.chartPages.chart?.datasetId);
  const current = chartDatasetId ? copy.datasets[chartDatasetId] : null;
  if (current) {
    const scene = spec.chart.type === 'wafer' ? wafer(current, spec.chart.wafer_z?.name ?? spec.chart.y_fields[0]?.name ?? 'qs')
      : buildJmpChartScene({ chartType: spec.chart.type, rows: current.rows, xFields: spec.chart.x_fields.map(field), yFields: spec.chart.y_fields.map((value) => field(value.name)), zField: spec.chart.z_field ? field(spec.chart.z_field.name) : null, showDataPoints: spec.chart.show_data_points });
    if (scene.placeholder) throw new Error(`报告图无法生成：${scene.placeholder}`);
    figures.push({ title: current.name, scene });
  }
  const comparison = spec.comparison;
  let compared: unknown = null;
  if (comparison) {
    const pair = (['a', 'b'] as const).map((side) => comparison.scope === 'workspace_snapshot' ? copy.datasets[comparison[side].dataset_id!]
      : [...datasets].reverse().find((dataset) => dataset.stats?.comparison_role === side && dataset.stats?.comparison_key === JSON.stringify([comparison, spec.quality_rule])));
    const [a, b] = pair;
    if (!a || !b) throw new Error('报告比较尚未完整载入，请先完成比较');
    const summary = (dataset: typeof a) => {
      const { rows: _rows, groups, ...values } = summarizeCohort(dataset.rows, summaries.find((s) => s.id === dataset.id)!.manifest, comparison, spec.quality_rule);
      return { ...values, groups: groups.map(({ rows: _members, ...group }) => group) };
    };
    compared = { a: summary(a), b: summary(b) };
    for (const metric of comparison.metrics) {
      const unit = spec.units[metric] ?? '未知单位';
      const domain = comparisonColorDomain(comparisonWaferCells(a, metric), comparisonWaferCells(b, metric), metric);
      for (const [index, dataset] of [a, b].entries()) {
        figures.push({ title: `${index ? 'B' : 'A'} · ${metric} 分布`, scene: distributionScene(dataset, metric, unit, spec.chart.type === 'violin' ? 'violin' : 'box') });
        figures.push({ title: `${index ? 'B' : 'A'} · ${metric} wafer 均值`, scene: wafer(dataset, metric, domain) });
      }
      figures.push({ title: `${metric} B − A 分组均值差`, scene: buildJmpChartScene({ chartType: 'scatter', rows: differenceRows(a, b, metric, comparison.group_fields), xFields: [{ name: '_group_label', label: '共同分组', isCategorical: true }], yFields: [{ name: 'mean_delta', label: 'B − A', unit, isCategorical: false }] }) });
    }
  }
  if (figures.length > 20) throw new Error('报告超过20图；请减少报告指标，不能省略图片后声称完整');
  const input = { project: copy, notes, generatedAt, summaries, comparison: compared, figures,
    partial: summaries.some((value) => value.manifest.truncated || value.manifest.sampling.method !== 'none' || value.manifest.total_relation !== 'exact' || value.manifest.consistency !== 'verified' || !!value.manifest.local_edits || !!value.manifest.excluded_rows) };
  return deepFreeze(input);
}
function deepFreeze<T>(value: T): T {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    for (const child of Object.values(value)) deepFreeze(child);
    Object.freeze(value);
  }
  return value;
}
export type FrozenReportInput = ReturnType<typeof freezeReportInput>;
