import React, { useState } from 'react';
import { useCapabilities } from '../api/capabilities';
import { serializeWorksheetCsv } from '../features/analysis/csv';
import type { AnalysisDataset } from '../features/analysis/model/dataset';
import useExportJob from './useExportJob';
import ExportJobProgress, { saveExportBlob } from './ExportJobProgress';
export default function ExportDialog({ dataset, onClose }: { dataset: AnalysisDataset; onClose: () => void }) {
  const { capabilities } = useCapabilities(), job = useExportJob();
  const [scope, setScope] = useState<'workspace' | 'source'>('workspace'), [format, setFormat] = useState<'csv' | 'xlsx'>('csv');
  const manifest = dataset.manifest;
  const verified = !!manifest && manifest.consistency === 'verified' && manifest.source_refs.every((source) => source.source_revision != null);
  return <div role="dialog" aria-label="导出数据" className="chart-card"><h3>导出数据</h3>
    <label><input type="radio" checked={scope === 'workspace'} disabled={!!job.state} onChange={() => { setScope('workspace'); setFormat('csv'); }} />当前工作区（含本地修改与排除）</label>
    <label><input type="radio" checked={scope === 'source'} disabled={!!job.state} onChange={() => setScope('source')} />全部筛选结果（固定版本源数据）</label>
    <p>工作区 {dataset.rows.length} 行，局部修改 {dataset.edits.length}，排除 {dataset.exclusions.length}；源匹配总数 {manifest?.matched_total ?? '未知'}。源导出忽略绘图限制、局部编辑及排除。</p>
    <pre>{JSON.stringify(manifest, null, 2)}</pre>
    {scope === 'source' && <><label>格式<select value={format} disabled={!!job.state} onChange={(event) => setFormat(event.target.value as 'csv' | 'xlsx')}><option value="csv">CSV</option><option value="xlsx" disabled={!capabilities?.features.full_export_xlsx}>XLSX（完整源数据）</option></select></label>{!verified && <p role="alert">来源未知/未验证，禁止完整源导出；仍可导出当前工作区。</p>}</>}
    <button disabled={!!job.state || (scope === 'source' && (!verified || !capabilities?.features[format === 'csv' ? 'full_export_csv' : 'full_export_xlsx']))} onClick={() => { if (scope === 'workspace') saveExportBlob(new Blob([serializeWorksheetCsv(dataset.rows)], { type: 'text/csv;charset=utf-8' }), `${dataset.name}.csv`); else void job.start(manifest!, format); }}>{scope === 'workspace' ? '下载当前工作区 CSV' : '创建完整源导出'}</button>
    <ExportJobProgress state={job.state} error={job.error} cancel={() => void job.cancel()} />
    <button onClick={onClose}>关闭</button>
  </div>;
}
