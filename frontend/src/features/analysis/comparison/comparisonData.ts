import type { AnalysisComparison, QualityRuleV1, ResultManifest } from '../../../types/platformContracts';
import type { AnalysisDataset } from '../model/dataset';
import { parseManifest } from '../project/projectCodec';
import { unverifiedManifest } from '../result/resultManifest';
import { attachSourceRefs, sourceTuple } from '../model/provenance';
import { IS_STATIC } from '../../../config';
import api from '../../../api/client';
import { computeMetricSummary, computeMeanDifferences, metricGroups, summarizeQuality, type Row } from './comparisonStatistics';
export interface ComparisonCohort {
  rows: Row[]; manifest: ResultManifest; statistics: ReturnType<typeof summaries>;
  groups: ReturnType<typeof metricGroups>; quality: ReturnType<typeof summarizeQuality>['quality'];
  pf: ReturnType<typeof summarizeQuality>['pf'];
}
function summaries(rows: Row[], metrics: string[]) {
  return Object.fromEntries(metrics.map((metric) => [metric, computeMetricSummary(rows, metric)]));
}
export function summarizeCohort(rows: Row[], manifest: ResultManifest, comparison: AnalysisComparison, rule: QualityRuleV1 | null): ComparisonCohort {
  return { rows, manifest, statistics: summaries(rows, comparison.metrics), groups: metricGroups(rows, comparison.metrics, comparison.group_fields), ...summarizeQuality(rows, rule) };
}
export function comparisonResult(a: ComparisonCohort, b: ComparisonCohort, comparison: AnalysisComparison, units: Record<string, string>) {
  return { schema_version: 1 as const, cohorts: { a, b }, units,
    deltas: computeMeanDifferences(a.rows, b.rows, comparison.metrics, comparison.group_fields) };
}
function object(value: unknown) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('比较响应格式无效');
  return Object.fromEntries(Object.entries(value));
}
export async function loadComparisonCohorts(comparison: AnalysisComparison, rule: QualityRuleV1 | null, datasets: AnalysisDataset[], units: Record<string, string>) {
  for (const predicate of rule?.predicates ?? []) {
    if (!Number.isFinite(predicate.value) || !units[predicate.field] || units[predicate.field] !== predicate.unit) throw new Error(`规则单位未知或不同：${predicate.field}`);
  }
  if (comparison.scope === 'workspace_snapshot') {
    const cohorts = (['a', 'b'] as const).map((side) => {
      const dataset = datasets.find((d) => d.id === comparison[side].dataset_id);
      if (!dataset) throw new Error('工作区快照已不存在');
      // Capture both row/ledger versions synchronously before any await.
      const rows = structuredClone(dataset.rows), manifest = structuredClone(dataset.manifest
        ?? unverifiedManifest(comparison[side].query_spec, rows.length));
      manifest.returned = rows.length; manifest.local_edits = dataset.edits.length; manifest.excluded_rows = dataset.exclusions.length;
      return summarizeCohort(rows, manifest, comparison, rule);
    });
    return comparisonResult(cohorts[0], cohorts[1], comparison, units);
  }
  if (IS_STATIC) return (await import('../../../api/static/comparison')).loadStaticComparison(comparison, rule, units);
  const response: unknown = (await api.post('/analysis/compare', { comparison, quality_rule: rule })).data;
  const result = object(response), cohortData = object(result.cohorts), serverUnits = object(result.units);
  if (result.schema_version !== 1) throw new Error('比较协议版本不支持');
  for (const metric of comparison.metrics) if (serverUnits[metric] !== units[metric]) throw new Error(`比较单位不一致：${metric}`);
  const cohorts = (['a', 'b'] as const).map((side) => {
    const value = object(cohortData[side]), expected = comparison[side].source;
    const manifest = parseManifest(value.manifest);
    if (!Array.isArray(value.rows) || manifest.returned !== value.rows.length || manifest.truncated || manifest.sampling.method !== 'none'
      || manifest.total_relation !== 'exact' || manifest.matched_total !== value.rows.length || manifest.consistency !== 'verified') throw new Error('源比较不是完整已验证结果');
    const rows = value.rows.map(object), refs = attachSourceRefs(rows, manifest);
    if (new Set(refs.map((ref) => ref ? sourceTuple(ref) : null)).size !== rows.length) throw new Error('比较器件身份重复');
    if (!expected || manifest.source_refs.length !== 1 || sourceTuple(manifest.source_refs[0]) !== sourceTuple(expected)
      || manifest.source_refs[0].source_revision !== expected.source_revision || refs.some((ref) => !ref || ref.source_revision !== expected.source_revision || !ref.source_device_key)) throw new Error('比较来源版本不一致');
    const calculated = summarizeCohort(rows, manifest, comparison, rule), stats = object(value.statistics);
    for (const metric of comparison.metrics) {
      const actual = object(stats[metric]);
      for (const [key, number] of Object.entries(calculated.statistics[metric])) {
        const remote = actual[key];
        if (number === null ? remote !== null : typeof remote !== 'number' || !Number.isFinite(remote)
          || Math.abs(remote - number) > Math.max(1e-9, Math.abs(number) * 1e-8)) throw new Error('服务端与客户端统计对账失败');
      }
    }
    return calculated;
  });
  return comparisonResult(cohorts[0], cohorts[1], comparison, units);
}
