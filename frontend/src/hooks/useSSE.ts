import { useEffect, useState } from 'react';
import { getTask } from '../api/endpoints';
import { TaskEventGate, terminalTask, type TaskSnapshot } from '../tasks/taskStatus';
import type { SSEProgressEvent } from '../types';
async function resolveStreamUrl(taskId: string | number) {
  if (window.electronAPI?.getBackendUrl) return `${await window.electronAPI.getBackendUrl()}/api/tasks/${taskId}/stream`;
  return `${window.location.protocol === 'file:' ? 'http://127.0.0.1:8000/api' : '/api'}/tasks/${taskId}/stream`;
}
const initial = { event: null as { type: string; data: SSEProgressEvent } | null, progress: 0, stage: '', stageProgress: 0, message: '', status: 'pending', error: null as string | null, done: false };
export default function useSSE(taskId: string | number | null | undefined, { enabled = true }: { enabled?: boolean } = {}) {
  const [state, setState] = useState(initial);
  useEffect(() => {
    setState(initial);
    if (taskId == null || !enabled) return;
    let disposed = false, reading = false;
    let stream: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const gate = new TaskEventGate();
    const accept = (data: TaskSnapshot, authoritative = false) => {
      if (disposed || !gate.accept(data, authoritative)) return;
      const done = terminalTask(gate.status);
      setState((old) => ({ event: { type: done ? 'done' : 'progress', data }, status: gate.status, progress: data.progress?.pct ?? data.progress_pct ?? old.progress, stage: data.stage ?? old.stage, stageProgress: data.progress?.stage_pct ?? data.stage_progress_pct ?? old.stageProgress, message: data.progress?.message ?? data.progress_msg ?? old.message, error: data.error_msg ?? null, done }));
      if (done) { stream?.close(); if (timer) clearTimeout(timer); }
    };
    const poll = async () => {
      if (disposed || reading || terminalTask(gate.status)) return;
      reading = true; const sequence = gate.sequence;
      try { const data = await getTask(taskId); if (gate.sequence === sequence) accept(data, true); }
      catch { if (!disposed) setState((old) => ({ ...old, error: '连接中断，任务状态未确认；正在重新查询' })); }
      finally { reading = false; if (!disposed && !terminalTask(gate.status)) timer = setTimeout(poll, 1000); }
    };
    const connect = async () => {
      await poll();
      if (disposed || terminalTask(gate.status)) return;
      try {
        const url = await resolveStreamUrl(taskId); if (disposed) return;
        stream = new EventSource(url);
        for (const name of ['progress', 'done', 'error']) stream.addEventListener(name, (event) => {
          try { const data = JSON.parse((event as MessageEvent).data); accept(data); }
          catch { /* Transport failure has no task status. GET polling remains active. */ }
        });
        stream.onerror = () => { stream?.close(); if (!disposed && !terminalTask(gate.status)) setState((old) => ({ ...old, error: '事件连接中断，正在查询任务状态' })); };
        stream.addEventListener('stream_closed', () => stream?.close());
      } catch { /* GET polling also covers unavailable EventSource. */ }
    };
    void connect();
    return () => { disposed = true; stream?.close(); if (timer) clearTimeout(timer); };
  }, [taskId, enabled]);
  return state;
}
