import { useEffect, useRef, useState } from 'react';
import { baseURL } from '../api/client.js';

export default function useSSE(taskId, { enabled = true } = {}) {
  const [event, setEvent] = useState(null);
  const [progress, setProgress] = useState(0);
  const [stage, setStage] = useState('extract');
  const [stageProgress, setStageProgress] = useState(0);
  const [message, setMessage] = useState('');
  const [status, setStatus] = useState('pending');
  const [error, setError] = useState(null);
  const [done, setDone] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!taskId || !enabled) return;
    const url = `${baseURL}/tasks/${taskId}/stream`;
    const es = new EventSource(url);
    ref.current = es;

    es.addEventListener('progress', (e) => {
      try {
        const data = JSON.parse(e.data);
        setEvent({ type: 'progress', data });
        if (typeof data.progress_pct === 'number') setProgress(data.progress_pct);
        if (typeof data.stage_progress_pct === 'number') setStageProgress(data.stage_progress_pct);
        if (data.stage) setStage(data.stage);
        if (data.progress_msg) setMessage(data.progress_msg);
        setStatus('running');
      } catch (_) {}
    });

    es.addEventListener('done', (e) => {
      try {
        const data = JSON.parse(e.data);
        setEvent({ type: 'done', data });
        setStatus(data.status || 'success');
        setStage(data.stage || 'done');
        setStageProgress(100);
        setProgress(100);
        setDone(true);
      } catch (_) {}
      es.close();
    });

    es.addEventListener('error', (e) => {
      try {
        const data = JSON.parse(e.data);
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
      // browser auto-retries; if connection truly fails we'll get error event above
    };

    return () => {
      es.close();
      ref.current = null;
    };
  }, [taskId, enabled]);

  return { event, progress, stage, stageProgress, message, status, error, done };
}
