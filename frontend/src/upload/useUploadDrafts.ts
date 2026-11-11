import { useCallback, useEffect, useRef, useState } from 'react';
import { prepareUploadDraft, getUploadDraft, getUploadGroup, commitUploadDraft, recheckUploadDraft, deleteUploadDraft, getTask, getProcessingReport } from '../api/endpoints';
import { useUploadProgress } from '../contexts/UploadProgressContext';
import { activeTask, isHiddenUploadTask, terminalTask } from '../tasks/taskStatus';
import { fileKey, uploadError, validateSelection, type UploadOptions, type UploadEntry } from './types';
import { createUuid } from '../utils/uuid';
const preference = 'aln_upload_group:';
function initialGroup() { try { return new URLSearchParams(window.location.search).get('groupId') || localStorage.getItem('aln_upload_last_group') || createUuid(); } catch { return createUuid(); } }
function metadata(group: string): UploadEntry[] { try { const values = JSON.parse(localStorage.getItem(preference + group) ?? '[]'); return Array.isArray(values) ? values.filter((value) => value && typeof value.key === 'string' && typeof value.name === 'string' && (typeof value.requestKey === 'string' || value.requestKey === null) && (typeof value.draftId === 'string' || value.draftId === null)) : []; } catch { return []; } }
export default function useUploadDrafts(files: File[], options: UploadOptions, onBusy?: (busy: boolean) => void) {
  const [groupId] = useState(initialGroup), [entries, setEntries] = useState<UploadEntry[]>(() => metadata(groupId)), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const rows = useRef(entries), alive = useRef(true), reading = useRef(false), writing = useRef(false);
  const { addTask, updateTask } = useUploadProgress();
  const update = useCallback((change: (current: UploadEntry[]) => UploadEntry[]) => {
    if (!alive.current) return;
    const next = change(rows.current); rows.current = next; setEntries(next);
    try { localStorage.setItem(preference + groupId, JSON.stringify(next.map(({ key, name, size, modified, requestKey, draftId, optionsKey }) => ({ key, name, size, modified, requestKey, draftId, optionsKey })))); localStorage.setItem('aln_upload_last_group', groupId); } catch { if (alive.current) setError('请求标识无法本地保存；当前会话可继续，刷新后请使用原会话恢复提交'); }
  }, [groupId]);
  const refresh = useCallback(async () => {
    if (reading.current || !alive.current) return; reading.current = true;
    try {
      const group = await getUploadGroup(groupId);
      if (!alive.current) return;
      if (group.group_id !== groupId || !Array.isArray(group.drafts)) throw new Error('上传组响应无效');
      const hidden = new Set<string>(group.drafts.filter((draft) => isHiddenUploadTask(draft.consumed_task?.status)).map((draft) => draft.draft_id));
      const incoming = await Promise.all(group.drafts.filter((draft) => !hidden.has(draft.draft_id)).map(async (draft) => {
        const old = rows.current.find((entry) => entry.draftId === draft.draft_id);
        const entry: UploadEntry = { ...(old ?? { key: draft.draft_id, name: draft.suggested_batch_no, size: 0, modified: 0, requestKey: null, draftId: draft.draft_id, optionsKey: null }), draft };
        if (draft.consumed_task_id) {
          try {
            if (!old?.task || activeTask(old.task.status) || activeTask(draft.consumed_task?.status)) {
              entry.task = await getTask(draft.consumed_task_id);
            }
            const status = entry.task?.status ?? draft.consumed_task?.status;
            if (alive.current && terminalTask(status) && !old?.report) entry.report = await getProcessingReport(draft.consumed_task_id);
          }
          catch (failure) { entry.error = `状态未确认：${uploadError(failure)}`; }
        }
        return entry;
      }));
      const visible = incoming.filter((entry) => {
        const hide = isHiddenUploadTask(entry.task?.status ?? entry.draft?.consumed_task?.status);
        if (hide && entry.draftId) hidden.add(entry.draftId);
        return !hide;
      });
      update((current) => [...current.filter((entry) => !hidden.has(entry.draftId ?? '') && !visible.some((item) => item.draftId === entry.draftId)), ...visible]);
      if (alive.current) for (const entry of visible) if (entry.draft?.consumed_task) {
        const task = entry.draft.consumed_task, status = entry.task?.status ?? task.status;
        const updates = { status, quality_status: entry.report?.quality_status, summary: entry.report?.summary, done: terminalTask(status) };
        if (!updates.done) addTask({ ...task, group_id: groupId, run_id: (entry.task as any)?.run_id });
        updateTask(task.task_id, updates);
      }
    } catch (failure) { if (alive.current) setError(uploadError(failure)); }
    finally { reading.current = false; }
  }, [groupId, update, addTask, updateTask]);
  useEffect(() => {
    alive.current = true; void refresh(); const timer = setInterval(() => {
      const active = rows.current.some((entry) => entry.draft?.status === 'preparing' || activeTask(entry.task?.status ?? entry.draft?.consumed_task?.status));
      if (active) void refresh();
    }, 1000);
    const url = new URL(window.location.href); url.searchParams.set('groupId', groupId); window.history.replaceState(window.history.state, '', url);
    return () => { alive.current = false; clearInterval(timer); };
  }, [refresh, groupId]);
  const action = async (operation: () => Promise<void>) => {
    if (writing.current) return; writing.current = true; setBusy(true); setError(''); onBusy?.(true);
    try { await operation(); } catch (failure) { if (alive.current) setError(uploadError(failure)); }
    finally { writing.current = false; if (alive.current) { setBusy(false); onBusy?.(false); await refresh(); } }
  };
  const prepare = () => action(async () => {
    validateSelection(files, options); if (!files.length) throw new Error('请选择文件');
    const optionsKey = JSON.stringify(options);
    for (const file of files) {
      if (!alive.current) return;
      const key = fileKey(file); let entry = rows.current.find((row) => row.key === key);
      if (entry?.draftId) {
        try {
          const draft = await getUploadDraft(entry.draftId);
          if (draft.draft_id !== entry.draftId || draft.group_id !== groupId) throw new Error('草稿响应无效，请刷新后重试');
          update((current) => current.map((row) => row.key === key ? { ...row, draft, error: draft.status === 'consumed' ? '此文件已提交，请查看入库任务。' : draft.status === 'preparing' ? '此文件正在检查，请等待检查完成。' : '此文件已有检查记录；请查看报告，或点击“复查此文件”。' } : row));
          continue;
        } catch (failure) {
          // Only an explicit 404 proves the cached draft is gone. Network errors
          // must not discard request identity or cause a duplicate upload.
          if ((failure as any)?.response?.status !== 404) throw failure;
          entry = { ...entry, draftId: null, draft: undefined, task: undefined, report: undefined, error: '' };
          const recovered = entry;
          update((current) => current.map((row) => row.key === key ? recovered : row));
        }
      }
      if (!entry) { entry = { key, name: file.name, size: file.size, modified: file.lastModified, requestKey: createUuid(), draftId: null, optionsKey }; update((current) => [...current, entry!]); }
      update((current) => current.map((row) => row.key === key ? { ...row, phase: 'uploading', progress: 0, error: '' } : row));
      const body = new FormData(); body.append('file', file); body.append('group_id', groupId); body.append('request_key', entry.requestKey!);
      for (const [field, value] of Object.entries(options)) if (value !== null) body.append(field, String(value));
      try {
        const result = await prepareUploadDraft(body, (progress) => update((current) => current.map((row) => row.key === key ? { ...row, progress: progress.total ? Math.round(progress.loaded / progress.total * 100) : 0 } : row)));
        update((current) => current.map((row) => row.key === key ? { ...row, draftId: result.draft_id, phase: 'checking', optionsKey } : row));
      } catch (failure) { update((current) => current.map((row) => row.key === key ? { ...row, phase: 'failed', error: uploadError(failure) } : row)); }
    }
  });
  const discard = (entry: UploadEntry) => action(async () => {
    if (!entry.draftId) return;
    await deleteUploadDraft(entry.draftId);
    update((current) => current.filter((row) => row.key !== entry.key));
  });
  const recheck = (entry: UploadEntry) => action(async () => { validateSelection([], options); if (!entry.draftId) return; await recheckUploadDraft(entry.draftId, options); update((current) => current.map((row) => row.key === entry.key ? { ...row, optionsKey: JSON.stringify(options), error: '' } : row)); });
  const commit = (entry: UploadEntry, warnings: string[]) => action(async () => {
    if (!entry.draftId || !entry.requestKey || !entry.draft?.check_digest) throw new Error('缺少原始请求标识或检查摘要，不能提交');
    if (entry.optionsKey !== JSON.stringify(options)) throw new Error('设置已变化，请复查后重新确认警告');
    try { const accepted = await commitUploadDraft(entry.draftId, { request_key: entry.requestKey, check_digest: entry.draft.check_digest, acknowledged_warning_codes: warnings }); addTask({ ...accepted, group_id: groupId }); }
    catch (failure) { if ((failure as any)?.response?.data?.detail?.code === 'PRECHECK_STALE') { await recheckUploadDraft(entry.draftId, options); throw new Error('检查证据已变化，已重新检查；请审查新报告再提交'); } throw failure; }
  });
  return { groupId, entries, error, busy, prepare, commit, recheck, discard, refresh };
}
