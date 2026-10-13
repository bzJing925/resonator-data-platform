import type { AnalysisDataset } from '../model/dataset';
import { buildJmpChartScene } from '../chart/chartScene';
import { computeMetricSummary, computeMeanDifferences, finite, type Row } from './comparisonStatistics';
export function comparisonColorDomain(a: Row[], b: Row[], metric: string): [number, number] | null {
  let min = Infinity, max = -Infinity;
  for (const rows of [a, b]) for (const row of rows) { const value = row[metric]; if (finite(value)) { min = Math.min(min, value); max = Math.max(max, value); } }
  if (!Number.isFinite(min)) return null;
  if (min === max) {
    const padding = Math.max(Math.abs(min) * 1e-6, 1e-6);
    return [Math.max(-Number.MAX_VALUE, min - padding), Math.min(Number.MAX_VALUE, max + padding)];
  }
  return [min, max];
}
export function comparisonWaferCells(dataset: AnalysisDataset, metric: string) {
  const groups = new Map<string, typeof dataset.rows>();
  for (const row of dataset.rows) {
    if (!finite(row.x) || !finite(row.y)) continue;
    const key = JSON.stringify([row.wafer ?? null, row.x, row.y]);
    const members = groups.get(key) ?? []; members.push(row); groups.set(key, members);
  }
  return [...groups.values()].map((members) => { const summary = computeMetricSummary(members, metric); return ({ ...members[0],
    wafer: members[0].wafer ?? '未知wafer', [metric]: summary.mean,
    _cell_n: members.length, _cell_valid_n: summary.valid_n,
    __memberRowIds: members.map((row) => row.rowId), __memberCount: members.length }); });
}
export function distributionScene(dataset: AnalysisDataset, metric: string, unit: string, type: 'box' | 'violin') {
  return buildJmpChartScene({ chartType: type, rows: dataset.rows, xFields: [{ name: 's_param_port', label: '端口', isCategorical: true }],
    yFields: [{ name: metric, label: metric, unit, isCategorical: false }], datasetRevision: dataset.revision, height: 350 });
}
export function differenceRows(a: AnalysisDataset, b: AnalysisDataset, metric: string, groups: string[]) {
  return computeMeanDifferences(a.rows, b.rows, [metric], groups).map((delta, index) => ({ ...delta,
    rowId: `comparison-summary:${index}`, _group_label: JSON.stringify(delta.group) }));
}
export function groupMemberRows(dataset: AnalysisDataset, group: Row, metric: string) {
  return dataset.rows.filter((row) => finite(row[metric]) && Object.entries(group).every(([field, value]) => (row[field] ?? null) === value))
    .map((row) => row.rowId);
}
