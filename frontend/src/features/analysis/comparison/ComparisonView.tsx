import React, { useEffect, useRef, useState } from 'react';
import { UnifiedChartGrid, WaferMap } from '../../../components/Charts';
import type { AnalysisComparison, QualityRuleV1 } from '../../../types/platformContracts';
import { useAnalysisWorkspace } from '../workspace';
import { selectedRowsForDataset } from '../model/selection';
import { datumRowIds } from '../chart/chartInteraction';
import { RequestSequence } from '../result/resultManifest';
import { unverifiedManifest } from '../result/resultManifest';
import DataScopeBanner from '../result/DataScopeBanner';
import { loadComparisonCohorts, summarizeCohort } from './comparisonData';
import { comparisonColorDomain, comparisonWaferCells, differenceRows, groupMemberRows } from './comparisonScene';

export default function ComparisonView({ fields = [] }: { fields?: { name: string; unit?: string }[] }) {
  const { state, datasets, loadQueryRows, renameDataset, selectRows, updateSpec, deleteSelection, undo } = useAnalysisWorkspace();
  const comparison = state.spec.comparison;
  const [accepted, setAccepted] = useState<{ spec: AnalysisComparison; ids: [string, string]; units: Record<string, string>; key: string; rule: QualityRuleV1 | null } | null>(null);
  const [metric, setMetric] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false), [range, setRange] = useState('');
  const sequence = useRef(new RequestSequence());
  const key = JSON.stringify([comparison, state.spec.quality_rule]);
  useEffect(() => { sequence.current.invalidate(); setBusy(false); }, [key]);
  if (!comparison) return null;
  const calculate = async () => {
    const request = sequence.current.begin(); setBusy(true); setError('');
    try {
      const captured = structuredClone(comparison);
      const units = Object.fromEntries(fields.map((field) => [field.name, field.unit ?? '']));
      const result = await loadComparisonCohorts(captured, state.spec.quality_rule, datasets, units);
      if (!sequence.current.current(request)) return;
      const ids = (['a', 'b'] as const).map((side) => {
        if (captured.scope === 'workspace_snapshot') return captured[side].dataset_id!;
        const cohort = result.cohorts[side];
        const id = loadQueryRows(cohort.rows, { comparison_role: side, comparison_key: key }, cohort.manifest);
        renameDataset(id, `比较 ${side.toUpperCase()}`); return id;
      }) as [string, string];
      setAccepted({ spec: captured, ids, units: result.units, key, rule: structuredClone(state.spec.quality_rule) }); setMetric(captured.metrics[0]); setRange('');
    } catch (failure) { if (sequence.current.current(request)) setError(failure instanceof Error ? failure.message : '比较失败'); }
    finally { if (sequence.current.current(request)) setBusy(false); }
  };
  const pair = accepted?.ids.map((id) => datasets.find((dataset) => dataset.id === id));
  const a = pair?.[0], b = pair?.[1];
  const delta = a && b && accepted ? differenceRows(a, b, metric, accepted.spec.group_fields) : [];
  const cellsA = a ? comparisonWaferCells(a, metric) : [], cellsB = b ? comparisonWaferCells(b, metric) : [];
  const domain = comparisonColorDomain(cellsA, cellsB, metric);
  const unit = accepted?.units[metric] ?? '';
  const yFields = [{ name: metric, label: metric, unit, isCategorical: false }];
  const type = state.spec.chart.type === 'violin' ? 'violin' : 'box';
  return <section aria-label="比较结果">
    <button disabled={busy || state.readOnly} onClick={calculate}>{busy ? '比较中…' : '计算比较'}</button>
    {error && <p role="alert">{error}</p>}
    {accepted && a && b && <>
      <p>{accepted.key !== key ? '上次成功结果；新规格尚未计算。' : ''}当前已加载工作表；本地修改及排除会更新统计。</p>
      <label>比较指标<select value={metric} onChange={(event) => setMetric(event.target.value)}>{accepted.spec.metrics.map((value) => <option key={value}>{value}</option>)}</select></label>
      <label>分布类型<select value={type} onChange={(event) => updateSpec((spec) => ({ ...spec, chart: { ...spec.chart, type: event.target.value as 'box' | 'violin' } }))}><option value="box">箱线图</option><option value="violin">小提琴图</option></select></label>
      <p role="status">{range}</p><button disabled={state.readOnly} onClick={deleteSelection}>排除所选比较行</button><button disabled={state.readOnly} onClick={undo}>撤销比较修改</button>
      {[a, b].map((dataset, index) => {
        const summary = summarizeCohort(dataset.rows, { ...(dataset.manifest ?? unverifiedManifest(accepted.spec.a.query_spec, dataset.rows.length)), returned: dataset.rows.length, local_edits: dataset.edits.length, excluded_rows: dataset.exclusions.length }, accepted.spec, accepted.rule);
        const cells = index ? cellsB : cellsA;
        return <div key={`${index}:${dataset.id}`} data-testid={`comparison-cohort-${index}`}>
          <h3>{index ? 'B' : 'A'} · {dataset.name}</h3><DataScopeBanner manifest={dataset.manifest && { ...dataset.manifest, local_edits: dataset.edits.length, excluded_rows: dataset.exclusions.length }} rows={dataset.rows.length} />
          <pre>{JSON.stringify({ statistics: summary.statistics[metric], pf: summary.pf, quality: summary.quality }, null, 2)}</pre>
          <UnifiedChartGrid plotId={`comparison-distribution-${index}`} chartType={type} rows={dataset.rows} xFields={[{ name: 's_param_port', label: '端口', isCategorical: true }]} yFields={yFields} analysisLayout revision={dataset.id} datasetRevision={dataset.revision} selectedRowIds={selectedRowsForDataset(state.selection, dataset.id)} onPointClick={(datum) => selectRows(dataset.id, datumRowIds(datum), 'replace', 'chart')} onSelection={(event: any) => selectRows(dataset.id, (event?.points ?? []).flatMap((point: any) => datumRowIds(point.customdata)), 'replace', 'chart')} onDeselect={() => selectRows(dataset.id, [], 'replace', 'chart')} />
          <WaferMap plotId={`comparison-wafer-${index}`} rows={cells} valueField={metric} valueLabel={`${metric} (${unit}) · 均值`} facetField="wafer" facets={[...new Set(cells.map((cell) => cell.wafer))]} aggregated colorDomain={domain} revision={`${dataset.id}:${dataset.revision}`} selectedRowIds={selectedRowsForDataset(state.selection, dataset.id)} onPointClick={(datum) => selectRows(dataset.id, datumRowIds(datum), 'replace', 'chart')} onSelection={undefined} />
        </div>;
      })}
      <h3>B − A 分组均值差（无器件配对）</h3>
      <UnifiedChartGrid plotId="comparison-difference" chartType="scatter" rows={delta} xFields={[{ name: '_group_label', label: '共同分组', isCategorical: true }]} yFields={[{ name: 'mean_delta', label: 'B − A', unit, isCategorical: false }]} analysisLayout revision="comparison-difference" datasetRevision={a.revision + b.revision} onPointClick={(datum) => {
        const row = delta.find((item) => datumRowIds(datum).includes(item.rowId)); if (!row) return;
        const idsA = groupMemberRows(a, row.group, metric), idsB = groupMemberRows(b, row.group, metric);
        selectRows(a.id, idsA, 'replace', 'chart'); selectRows(b.id, idsB, 'addAcross', 'chart');
        setRange(`共同分组 ${JSON.stringify(row.group)}：A ${idsA.length} 行，B ${idsB.length} 行；仅 ${metric} 有效值。`);
      }} />
      {domain === null && <p>wafer 指标全部缺失，无颜色范围。</p>}
    </>}
  </section>;
}
