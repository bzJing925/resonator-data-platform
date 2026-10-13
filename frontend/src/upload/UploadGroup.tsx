import { taskLabel } from '../tasks/taskStatus';
import React from 'react';
import { Link } from 'react-router-dom';
import { useCapabilities } from '../api/capabilities';
import useUploadDrafts from './useUploadDrafts';
import PrecheckReport from './PrecheckReport';
import PrecheckProgress from './PrecheckProgress';
import IngestionReport from './IngestionReport';
import type { UploadOptions } from './types';
export default function UploadGroup({ files, options, onBusy }: { files: File[]; options: UploadOptions; onBusy?: (busy: boolean) => void }) {
  const { capabilities } = useCapabilities(), group = useUploadDrafts(files, options, onBusy);
  const terminal = group.entries.filter((entry) => ['success', 'failed', 'cancelled'].includes(entry.task?.status ?? '')).length;
  return <section className="chart-card workflow-panel" style={{ gridColumn: '1 / -1' }} aria-label="上传组">
    <h3>③ 检查与入库</h3>{group.entries.length > 0 && <p className="dim">{group.entries.length} 个文件 · 已结束 {terminal}</p>}
    {!capabilities?.features.upload_precheck && <p>上传检查能力不可用/未知；可读取既有组状态。</p>}
    <div className="workflow-actions"><button className="btn primary" disabled={group.busy || !capabilities?.features.upload_precheck || !files.length} onClick={group.prepare}>检查数据</button><button className="btn" disabled={group.busy} onClick={group.refresh}>刷新组状态</button></div>
    {group.error && <p role="alert">{group.error}</p>}
    {group.entries.map((entry) => {
      const expired = entry.draft && (entry.draft.status === 'expired' || Date.parse(entry.draft.expires_at) <= Date.now()) && entry.draft.status !== 'consumed';
      return <article className="workflow-entry" key={entry.key} data-testid={`upload-entry-${entry.name}`}><h4>{entry.name}</h4>
        {entry.draft?.status === 'preparing' && !entry.task && !expired ? <PrecheckProgress draft={entry.draft} /> : <p>{entry.task ? `${taskLabel(entry.task.status)} · ${entry.task.stage ?? ''} · ${entry.task.progress_pct ?? 0}%` : expired ? '草稿已过期，需复查或重新选择文件' : entry.draft ? `${{ preparing: '检查中', ready: '检查完成', failed: '检查失败', consumed: '已提交', expired: '已过期' }[entry.draft.status]} · ${entry.draft.prepare_progress}%` : entry.phase === 'uploading' ? `上传 ${entry.progress ?? 0}%` : entry.phase === 'failed' ? '上传失败' : '未上传，刷新后需重新选择原文件'}</p>}
        {entry.error && <p role="alert">{entry.error}</p>}
        {entry.draft && entry.draft.status !== 'consumed' && <>
          {!entry.requestKey && <p role="alert">原始请求标识未保存在当前浏览器，不能提交该草稿；请使用原浏览器恢复。</p>}
          {entry.draft.status !== 'preparing' && <PrecheckReport draft={entry.draft} disabled={group.busy || !!expired || !entry.requestKey || !capabilities?.features.upload_precheck} onCommit={(codes) => group.commit(entry, codes)} />}
          <button className="btn" disabled={group.busy || entry.draft.status === 'preparing' || !capabilities?.features.upload_precheck} onClick={() => group.recheck(entry)}>复查此文件</button>
        </>}
        {entry.draft?.consumed_task && <><Link to={`/batches/${encodeURIComponent(entry.draft.consumed_task.batch_no)}`}>查看批次 {entry.draft.consumed_task.batch_no}</Link><IngestionReport taskId={entry.draft.consumed_task.task_id} report={entry.report} status={entry.task?.status} /></>}
      </article>;
    })}
  </section>;
}
