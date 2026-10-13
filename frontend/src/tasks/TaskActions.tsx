import React, { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { cancelTask, getRetryOptions, getTask, retryTask, type RetryOptions } from '../api/endpoints';
import { useCapabilities } from '../api/capabilities';
import type { Task } from '../types';
import { uploadError } from '../upload/types';
import { activeTask, stopExplanation, terminalTask } from './taskStatus';
export default function TaskActions({ task, onUpdate }: { task: Task; onUpdate?: (task: Task) => void }) {
  const { capabilities } = useCapabilities();
  const [options, setOptions] = useState<RetryOptions | null>(null), [selected, setSelected] = useState<string[]>([]), [error, setError] = useState(''), [busy, setBusy] = useState(false), [blocked, setBlocked] = useState(false), [accepted, setAccepted] = useState<string | null>(null);
  const lock = useRef(false), key = useRef<string | null>(null), alive = useRef(true);
  useEffect(() => {
    alive.current = true; let cancelled = false;
    setOptions(null); setSelected([]); setError(''); setBlocked(false); setAccepted(null); key.current = null;
    if (capabilities?.features.processing_reports && task.run_id) getRetryOptions(task.id).then((value) => { if (!cancelled) { if (value.run_id !== task.run_id) setError('重试运行证据不匹配，禁止提交'); else setOptions(value); } }).catch((failure) => { if (!cancelled) setError(uploadError(failure)); });
    return () => { cancelled = true; alive.current = false; };
  }, [task.id, task.run_id, terminalTask(task.status), capabilities?.features.processing_reports]);
  const action = async (fn: () => Promise<void>) => {
    if (lock.current) return; lock.current = true; setBusy(true); setError('');
    try { await fn(); } catch (failure) { if (alive.current) { setError(uploadError(failure)); if ((failure as any)?.response?.status === 409) setBlocked(true); } }
    finally { lock.current = false; if (alive.current) setBusy(false); }
  };
  const stop = () => action(async () => {
    if (!window.confirm(`${stopExplanation}\n是否停止任务 ${task.id}？`)) return;
    const result = await cancelTask(task.id);
    if (alive.current) onUpdate?.(result);
    const confirmed = await getTask(task.id); if (alive.current) onUpdate?.(confirmed);
  });
  return <section aria-label={`任务 ${task.id} 操作`}>
    {task.status === 'cancel_requested' && <p>正在停止；输入与已发布结果保留。{task.error_msg?.includes('STOP_NOT_CONFIRMED') && '执行器尚未确认，请继续查询状态。'}</p>}
    {task.status === 'cancelled' && <p>已停止；输入与已发布结果保留。</p>}
    {activeTask(task.status) && <button className="btn fail" disabled={busy || task.status === 'cancel_requested' || !task.run_id || !capabilities?.features.processing_reports} onClick={stop}>停止处理</button>}
    {!task.run_id && <p>旧任务缺少运行证据，不提供新版安全停止或失败项重试。</p>}
    {options?.retry_of_task_id != null && <p>重试来源：<Link to={`/tasks/${options.retry_of_task_id}`}>任务 {options.retry_of_task_id}</Link></p>}
    {terminalTask(task.status) && task.run_id && <>
      <p>失败项重试 · 原任务来源版本 {options?.expected_source_revision ?? '未知'}。旧验收报告保持可查。</p>
      {options?.items.map((item) => <label key={item.id} style={{ display: 'block' }}><input type="checkbox" checked={selected.includes(item.id)} disabled={busy || blocked || !!accepted} onChange={(event) => { key.current = null; setSelected((old) => event.target.checked ? [...old, item.id] : old.filter((id) => id !== item.id)); }} />{item.source_member} · {item.port ?? '无端口'} · {item.status} · {item.error_code ?? ''}</label>)}
      {options?.next_offset != null && <button disabled={busy || blocked} onClick={() => action(async () => { const next = await getRetryOptions(task.id, options.next_offset!); if (next.run_id !== options.run_id || next.expected_source_revision !== options.expected_source_revision) throw new Error('重试证据已变化，请重新核对'); if (alive.current) setOptions({ ...next, items: [...options.items, ...next.items] }); })}>加载更多失败项</button>}
      <button disabled={busy || blocked || !!accepted || !options?.items.length || options.next_offset != null} onClick={() => { key.current = null; setSelected(options!.items.map((item) => item.id)); }}>选择全部失败项</button>
      <button className="btn" disabled={busy || blocked || !!accepted || !selected.length || options?.expected_source_revision == null || !capabilities?.features.processing_reports} onClick={() => action(async () => {
        key.current ??= crypto.randomUUID(); const result = await retryTask(task.id, { request_key: key.current, mode: 'failed', item_ids: selected, expected_source_revision: options!.expected_source_revision! });
        if (alive.current) setAccepted(result.task_id);
      })}>重试所选失败项</button>
    </>}
    {blocked && <p role="alert">输入或版本冲突；已保留选项。请重新核对或从批次入口发起整批新处理，不自动更换版本。</p>}
    {accepted && <p><Link to={`/tasks/${accepted}`}>查看新重试任务 {accepted}</Link>；原任务与报告仍保留。</p>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
