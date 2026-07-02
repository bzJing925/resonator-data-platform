import { useEffect, useRef, useState } from 'react';
import type { SSEProgressEvent } from '../types';

async function resolveStreamUrl(taskId: string | number): Promise<string> {
  if (window.electronAPI?.getBackendUrl) {
    const backendUrl = await window.electronAPI.getBackendUrl();
    return `${backendUrl}/api/tasks/${taskId}/stream`;
  }
  const isElectronFile = typeof window !== 'undefined' && window.location.protocol === 'file:';
  const base = isElectronFile ? 'http://127.0.0.1:8000/api' : '/api';
  return `${base}/tasks/${taskId}/stream`;
}

interface UseSSEOptions {
  enabled?: boolean;
}

interface UseSSEReturn {
  event: { type: string; data: SSEProgressEvent } | null;
  progress: number;
  stage: string;
  stageProgress: number;
  message: string;
  status: string;
  error: string | null;
  done: boolean;
}

export default function useSSE(
  taskId: string | number | null,
  { enabled = true }: UseSSEOptions = {}
): UseSSEReturn {
  const [event, setEvent] = useState<{ type: string; data: SSEProgressEvent } | null>(null);
  const [progress, setProgress] = useState(0);
  const [stage, setStage] = useState('extract');
  const [stageProgress, setStageProgress] = useState(0);
  const [message, setMessage] = useState('');
  const [status, setStatus] = useState('pending');
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const retryCountRef = useRef(0);

  useEffect(() => {
    if (!taskId || !enabled) return;

    let cancelled = false;

    const connect = async () => {
      const url = await resolveStreamUrl(taskId);
      const es = new EventSource(url);
      esRef.current = es;

      es.addEventListener('progress', (e: Event) => {
        try {
          const data = JSON.parse((e as MessageEvent).data) as SSEProgressEvent;
          setEvent({ type: 'progress', data });
          if (typeof data.progress_pct === 'number') setProgress(data.progress_pct);
          if (typeof data.stage_progress_pct === 'number') setStageProgress(data.stage_progress_pct);
          if (data.stage) setStage(data.stage);
          if (data.progress_msg) setMessage(data.progress_msg);
          setStatus('running');
        } catch (_) {}
      });

      es.addEventListener('done', (e: Event) => {
        try {
          const data = JSON.parse((e as MessageEvent).data) as SSEProgressEvent;
          setEvent({ type: 'done', data });
          setStatus(data.status || 'success');
          setStage(data.stage || 'done');
          setStageProgress(100);
          setProgress(100);
          setDone(true);
        } catch (_) {}
        es.close();
      });

      es.addEventListener('error', (e: Event) => {
        try {
          const data = JSON.parse((e as MessageEvent).data) as SSEProgressEvent;
          setEvent({ type: 'error', data });
          setError(data.error_msg || '任务错误');
          setStatus('error');
        } catch (_) {
          setError('连接错误');
        }
        setDone(true);
        es.close();
      });

      es.onerror = () => {
        if (cancelled || done) return;
        es.close();
        retryCountRef.current += 1;
        const delay = Math.min(30000, 2 ** retryCountRef.current * 1000);
        setTimeout(() => {
          if (!cancelled) connect();
        }, delay);
      };
    };

    connect();

    return () => {
      cancelled = true;
      esRef.current?.close();
      esRef.current = null;
    };
  }, [taskId, enabled]);

  return { event, progress, stage, stageProgress, message, status, error, done };
}
