import TechnicalDetails from '../components/TechnicalDetails';
import React, { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { getProcessingReport, downloadProcessingIssues } from '../api/endpoints';
import { uploadError, type ProcessingReport } from './types';
export default function IngestionReport({ taskId, report, status }: { taskId: string | number; report?: ProcessingReport; status?: string }) {
  const [value, setValue] = useState<ProcessingReport | null>(report ?? null), [error, setError] = useState('');
  const pending = useRef(false), alive = useRef(true);
  useEffect(() => { alive.current = true; if (report) setValue(report); else void getProcessingReport(taskId).then((result) => { if (alive.current) setValue(result); }).catch((failure) => { if (alive.current) setError(uploadError(failure)); }); return () => { alive.current = false; }; }, [taskId, report, status]);
  const download = async () => {
    try { const { data } = await downloadProcessingIssues(taskId); const url = URL.createObjectURL(data); const anchor = document.createElement('a'); anchor.href = url; anchor.download = `task-${taskId}-issues.csv`; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
    catch (failure) { setError(uploadError(failure)); }
  };
  return <section aria-label={`任务${taskId}验收单`}><h4>任务 {taskId} 验收单</h4><Link to={`/tasks/${taskId}`}>任务详情</Link>
    {error && <p role="alert">{error}</p>}{!value ? <p>验收结果未提供/读取中</p> : <>
      <p>质量：{value.quality_status === 'partial' ? '部分成功（存在失败/跳过/警告）' : value.quality_status === 'complete' ? '完整' : value.quality_status === 'invalid' ? '无有效结果' : '未知/未完成'}；实际发布 {value.summary.published_count ?? '未知'} 器件。</p>
      <TechnicalDetails value={{ file_counts: value.summary.file_counts ?? '未知', device_counts: value.summary.device_counts ?? '未知', issue_counts: value.summary.issue_counts ?? '未知' }} />
      <ul>{value.issues.map((issue) => <li key={issue.id}>{issue.code} · {issue.message}</li>)}</ul>
      {value.next_offset !== null && <button className="btn" onClick={async () => { if (pending.current) return; pending.current = true; try { const next = await getProcessingReport(taskId, value.next_offset!); if (alive.current) setValue({ ...next, issues: [...value.issues, ...next.issues] }); } catch (failure) { if (alive.current) setError(uploadError(failure)); } finally { pending.current = false; } }}>读取更多问题</button>}
      <button className="btn" onClick={download}>下载全部问题CSV</button>
    </>}
  </section>;
}
