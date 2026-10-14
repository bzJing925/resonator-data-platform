import { useEffect, useRef, useState } from 'react';
import { createExportJob, getExportJob, cancelExportJob } from '../api/endpoints';
import { uploadError } from '../upload/types';
import type { ResultManifest } from '../types/platformContracts';
import { terminalTask } from '../tasks/taskStatus';
export interface ExportState { id?: string; status: string; progress_pct?: number; rows?: number; manifest?: ResultManifest; artifacts?: { name: string; download_url: string }[]; expires_at?: string; error?: unknown; csv?: Blob }
export default function useExportJob() {
  const [state, setState] = useState<ExportState | null>(null), [error, setError] = useState('');
  const stopping = useRef(false);
  const lock = useRef(false), alive = useRef(true), key = useRef<string | null>(null);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
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
      const accepted = await createExportJob({ request_key: key.current, format, query_spec: captured.query_spec, expected_sources: captured.source_refs }); if (alive.current) setState(accepted);
    } catch (failure) { if (alive.current) setError(uploadError(failure)); } finally { lock.current = false; }
  };
  const cancel = async () => { if (state?.id && !lock.current) { lock.current = true; try { const result = await cancelExportJob(state.id); if (alive.current) { stopping.current = result.status === 'cancel_requested'; setState(result); } } catch (failure) { if (alive.current) setError(uploadError(failure)); } finally { lock.current = false; } } };
  return { state, error, start, cancel };
}
