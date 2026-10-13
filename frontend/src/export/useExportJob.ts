import { useEffect, useRef, useState } from 'react';
import { createExportJob, getExportJob, cancelExportJob } from '../api/endpoints';
import { IS_STATIC } from '../config';
import { uploadError } from '../upload/types';
import type { ResultManifest } from '../types/platformContracts';
import { terminalTask } from '../tasks/taskStatus';
export interface ExportState { id?: string; status: string; progress_pct?: number; rows?: number; manifest?: ResultManifest; artifacts?: { name: string; download_url: string }[]; expires_at?: string; error?: unknown; csv?: Blob }
export default function useExportJob() {
  const [state, setState] = useState<ExportState | null>(null), [error, setError] = useState('');
  const stopping = useRef(false);
  const lock = useRef(false), alive = useRef(true), worker = useRef<Worker | null>(null), key = useRef<string | null>(null);
  useEffect(() => { alive.current = true; return () => { alive.current = false; worker.current?.terminate(); }; }, []);
  useEffect(() => {
    if (!state?.id || terminalTask(state.status)) return;
    let stopped = false; let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => { try { const next = await getExportJob(state.id!); if (!stopped && (!stopping.current || ['cancel_requested', 'cancelled'].includes(next.status))) { setState(next); setError(''); } } catch (failure) { if (!stopped) setError(uploadError(failure)); } finally { if (!stopped) timer = setTimeout(poll, 1000); } };
    void poll(); return () => { stopped = true; if (timer) clearTimeout(timer); };
  }, [state?.id, terminalTask(state?.status)]);
  const start = async (manifest: ResultManifest, format: 'csv' | 'xlsx') => {
    if (lock.current || state) return; lock.current = true; setError(''); key.current ??= crypto.randomUUID();
    const captured = structuredClone(manifest);
    try {
      if (captured.consistency !== 'verified' || captured.source_refs.some((source) => source.source_revision == null)) throw new Error('来源未验证，不能完整源导出');
      if (IS_STATIC) {
        if (format !== 'csv') throw new Error('静态版不支持完整XLSX');
        setState({ status: 'running', rows: 0 }); const instance = new Worker(new URL('../api/static/fullExportWorker.ts', import.meta.url), { type: 'module' }); worker.current = instance;
        instance.onerror = () => { if (alive.current) { setState({ status: 'failed' }); setError('DuckDB Worker失败/内存不足，未生成下载'); } instance.terminate(); };
        instance.onmessage = (event) => { if (!alive.current) return; const message = event.data; if (message.type === 'progress') setState({ status: 'running', rows: message.rows }); else { if (message.type === 'success') setState({ status: 'success', rows: message.result.manifest.returned, ...message.result }); else { setState({ status: 'failed' }); setError(message.message); } instance.terminate(); } };
        instance.postMessage({ root: new URL('webdata/', document.baseURI).href, query_spec: captured.query_spec, expected_sources: captured.source_refs });
      } else { const accepted = await createExportJob({ request_key: key.current, format, query_spec: captured.query_spec, expected_sources: captured.source_refs }); if (alive.current) setState(accepted); }
    } catch (failure) { if (alive.current) setError(uploadError(failure)); } finally { lock.current = false; }
  };
  const cancel = async () => { if (IS_STATIC) { if (worker.current) worker.current.onmessage = null; worker.current?.terminate(); worker.current = null; setState({ status: 'cancelled' }); } else if (state?.id && !lock.current) { lock.current = true; try { const result = await cancelExportJob(state.id); if (alive.current) { stopping.current = result.status === 'cancel_requested'; setState(result); } } catch (failure) { if (alive.current) setError(uploadError(failure)); } finally { lock.current = false; } } };
  return { state, error, start, cancel };
}
