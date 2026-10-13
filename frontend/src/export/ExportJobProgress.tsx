import React, { useState } from 'react';
import { downloadExportArtifact } from '../api/endpoints';
import { taskLabel, terminalTask } from '../tasks/taskStatus';
import { uploadError } from '../upload/types';
import type { ExportState } from './useExportJob';
export function saveExportBlob(blob: Blob, name: string) { const url = URL.createObjectURL(blob), anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
export default function ExportJobProgress({ state, error, cancel }: { state: ExportState | null; error: string; cancel: () => void }) {
  const [downloadError, setDownloadError] = useState('');
  const valid = state?.manifest?.consistency === 'verified' && state.manifest.total_relation === 'exact' && !state.manifest.truncated && state.manifest.sampling.method === 'none' && state.rows === state.manifest.returned && state.rows === state.manifest.matched_total;
  const expired = state?.expires_at && Date.parse(state.expires_at) <= Date.now();
  return <section>{state && <><p>{taskLabel(state.status)} · 行数 {state.rows ?? '未知'} · {state.progress_pct ?? 0}%</p>
    {state.status === 'cancel_requested' && <p>正在停止，尚未确认结束。</p>}
    {!terminalTask(state.status) && <button disabled={state.status === 'cancel_requested'} onClick={cancel}>停止导出</button>}
    {state.error != null && <p role="alert">{JSON.stringify(state.error)}</p>}
    {state.status === 'success' && !valid && <p role="alert">导出完整性证据不一致，禁止下载。</p>}
    {expired && <p role="alert">导出已过期，请重新查询并创建新任务。</p>}
    {state.status === 'success' && !expired && valid && <>
      <pre>{JSON.stringify(state.manifest, null, 2)}</pre>
      {state.csv && <><button onClick={() => saveExportBlob(state.csv!, 'data.csv')}>下载完整 CSV</button><button onClick={() => saveExportBlob(new Blob([JSON.stringify(state.manifest, null, 2)], { type: 'application/json' }), 'manifest.json')}>下载来源说明</button></>}
      {state.artifacts?.map((artifact) => <button key={artifact.name} onClick={async () => { try { const data = await downloadExportArtifact(state.id!, artifact.name); saveExportBlob(data, artifact.name); } catch (failure) { setDownloadError(uploadError(failure)); } }}>下载 {artifact.name}</button>)}
    </>}
  </>}{(error || downloadError) && <p role="alert">{error || downloadError}；分析数据仍保留。SOURCE_CHANGED需要重新查询，不自动更新来源版本。</p>}</section>;
}
