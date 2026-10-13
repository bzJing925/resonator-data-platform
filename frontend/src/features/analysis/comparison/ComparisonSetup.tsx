import React, { useEffect, useRef, useState } from 'react';
import { inspectComparisonConditions } from '../../../api/endpoints';
import type { AnalysisComparison, SourceRef } from '../../../types/platformContracts';
import { useAnalysisWorkspace } from '../workspace';
import { sourceTuple } from '../model/provenance';
import { RequestSequence } from '../result/resultManifest';
import { COMPARISON_METRICS, makeComparison, validateConditionInspection, type ConditionInspection, type Shape } from './comparisonSpec';
import { applyCommonSubset, inspectCompatibility } from './compatibility';

export default function ComparisonSetup({ fields = null }: { fields?: { name: string; unit?: string }[] | null }) {
  const { datasets, state, updateSpec } = useAnalysisWorkspace();
  const [scope, setScope] = useState<'source' | 'workspace_snapshot'>('workspace_snapshot');
  const [a, setA] = useState(''), [b, setB] = useState(''), [metrics, setMetrics] = useState<string[]>(['qs']);
  const [inspection, setInspection] = useState<ConditionInspection | null>(null);
  const [draft, setDraft] = useState<AnalysisComparison | null>(null);
  const [selected, setSelected] = useState<Shape[]>([]), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const sequence = useRef(new RequestSequence());
  useEffect(() => {
    sequence.current.invalidate(); setInspection(null); setDraft(null); setSelected([]); setBusy(false);
  }, [state.registry, state.spec.quality_rule, fields]);
  const units = Object.fromEntries((fields ?? []).map((field) => [field.name, field.unit ?? '']));
  const sources = new Map<string, { source: SourceRef; datasetId: string; label: string }>();
  for (const dataset of datasets) if (dataset.manifest?.consistency === 'verified') for (const ref of dataset.manifest.source_refs) {
    if (ref.source_device_key !== null || ref.source_revision === null) continue;
    const id = JSON.stringify([ref.origin_id, ref.source_batch_id, ref.source_revision, dataset.id]);
    sources.set(id, { source: ref, datasetId: dataset.id, label: `${dataset.name} · ${sourceTuple(ref)} · v${ref.source_revision}` });
  }
  const invalidate = () => { sequence.current.invalidate(); setInspection(null); setDraft(null); setSelected([]); setBusy(false); setError(''); };
  const prepare = () => {
    const endpoint = (value: string) => {
      if (scope === 'workspace_snapshot') {
        const dataset = datasets.find((d) => d.id === value);
        if (!dataset) throw new Error('请选择工作区数据表');
        return { source: null, dataset_id: dataset.id, query_spec: dataset.manifest?.query_spec ?? state.spec.query_spec };
      }
      const chosen = sources.get(value), dataset = datasets.find((d) => d.id === chosen?.datasetId);
      if (!chosen || !dataset?.manifest) throw new Error('来源版本未知，不能做源比较');
      return { source: chosen.source, dataset_id: null, query_spec: dataset.manifest.query_spec };
    };
    return makeComparison(endpoint(a), endpoint(b), scope, metrics);
  };
  const inspect = async () => {
    const request = sequence.current.begin(); setBusy(true); setError(''); setInspection(null); setDraft(null); setSelected([]);
    try {
      const comparison = prepare();
      if (scope === 'workspace_snapshot') {
        for (const metric of metrics) {
          if (!units[metric]) throw new Error(`指标单位未知：${metric}`);
          for (const id of [a, b]) if (!datasets.find((d) => d.id === id)?.columns.some((column) => column.key === metric)) throw new Error(`工作表缺少指标：${metric}`);
        }
        updateSpec((spec) => ({ ...spec, comparison, units: { ...spec.units, ...Object.fromEntries(metrics.map((m) => [m, units[m]])) } }));
        setDraft(comparison); return;
      }
      const result = validateConditionInspection(await inspectComparisonConditions({ comparison, quality_rule: state.spec.quality_rule }), comparison);
      if (!sequence.current.current(request)) return;
      setInspection(result); setDraft(comparison);
    } catch (e) {
      if (sequence.current.current(request)) setError(e instanceof Error ? e.message : '条件审查失败，输入保留');
    } finally { if (sequence.current.current(request)) setBusy(false); }
  };
  const review = inspection ? inspectCompatibility(inspection, metrics, units) : null;
  return <section aria-label="比较设置">
    <h3>批次/工作区比较</h3>
    <label>比较范围 <select aria-label="比较范围" value={scope} onChange={(e) => { invalidate(); setScope(e.target.value === 'source' ? 'source' : 'workspace_snapshot'); setA(''); setB(''); }}>
      <option value="workspace_snapshot">工作区快照（含本地编辑/排除）</option><option value="source">完整源查询（锁定版本）</option>
    </select></label>
    {(['a', 'b'] as const).map((side) => <label key={side}>来源 {side.toUpperCase()} <select aria-label={`比较来源${side.toUpperCase()}`} value={side === 'a' ? a : b} onChange={(e) => { invalidate(); (side === 'a' ? setA : setB)(e.target.value); }}>
      <option value="">请选择</option>{scope === 'workspace_snapshot' ? datasets.map((d) => <option key={d.id} value={d.id}>{d.name} · {d.id}</option>)
        : [...sources].map(([id, item]) => <option key={id} value={id}>{item.label}</option>)}
    </select></label>)}
    <fieldset><legend>指标</legend>{COMPARISON_METRICS.map((metric) => <label key={metric}><input type="checkbox" checked={metrics.includes(metric)} onChange={(e) => {
      invalidate(); setMetrics((previous) => e.target.checked ? [...previous, metric] : previous.filter((m) => m !== metric));
    }} />{metric} ({units[metric] || '单位未知'})</label>)}</fieldset>
    {scope === 'source' && !sources.size && <p>来源版本未知，源比较不可用。请先读取有验证说明的来源。</p>}
    {scope === 'workspace_snapshot' && <p>比较范围为当前工作表；截断、编辑、排除保留在各自结果说明中，不能作为完整源条件一致的证明。</p>}
    <button disabled={busy || !a || !b || !metrics.length || state.readOnly} onClick={() => void inspect()}>{busy ? '正在审查' : scope === 'source' ? '审查条件（不计算）' : '使用工作区快照规格'}</button>
    {error && <p role="alert">{error}</p>}
    {inspection && <>
      <div>来源A：<pre>{JSON.stringify(inspection.endpoints.a.conditions, null, 2)}</pre></div>
      <div>来源B：<pre>{JSON.stringify(inspection.endpoints.b.conditions, null, 2)}</pre></div>
      {review?.issues.map((issue, i) => <div role={issue.blocking ? 'alert' : undefined} key={i}><strong>{issue.field}：</strong>{issue.message}<pre>A={JSON.stringify(issue.a)}{'\n'}B={JSON.stringify(issue.b)}</pre></div>)}
      {review?.requiresSubset && <fieldset><legend>明确选择共同端口/结构元组</legend>{review.common.map((shape) => <label key={JSON.stringify(shape)}><input type="checkbox" checked={selected.some((x) => JSON.stringify(x) === JSON.stringify(shape))} onChange={(e) => setSelected((old) => e.target.checked ? [...old, shape] : old.filter((x) => JSON.stringify(x) !== JSON.stringify(shape)))} />{JSON.stringify(shape)}</label>)}</fieldset>}
      <button disabled={!draft || !review || review.blocked || (review.requiresSubset && !selected.length) || state.readOnly} onClick={() => {
        if (!draft || !review || review.blocked) return;
        const comparison = review.requiresSubset ? { ...draft, condition_policy: 'common_subset' as const,
          a: { ...draft.a, query_spec: applyCommonSubset(draft.a.query_spec, selected) }, b: { ...draft.b, query_spec: applyCommonSubset(draft.b.query_spec, selected) } } : draft;
        updateSpec((spec) => ({ ...spec, comparison, units: { ...spec.units, ...inspection.units } }));
      }}>确认比较规格</button>
    </>}
    {state.spec.comparison && <p role="status">比较规格已配置；计算时会再次核验来源版本和处理条件。</p>}
  </section>;
}
