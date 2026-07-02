import React, { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import I from '../components/Icons';
import { getTask } from '../api/endpoints';
import useSSE from '../hooks/useSSE';
import type { Task } from '../types';

export default function TaskDetail() {
  const { taskId } = useParams<{ taskId: string }>();
  const [task, setTask] = useState<Task | null>(null);
  const [error, setError] = useState<string | null>(null);
  const sse = useSSE(taskId);

  useEffect(() => {
    if (!taskId) return;
    let cancelled = false;
    getTask(taskId)
      .then((d) => { if (!cancelled) setTask(d); })
      .catch((e: Error) => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [taskId, sse.done]);

  const status = sse.status !== 'pending' ? sse.status : task?.status;
  const progress = sse.progress || task?.progress_pct || 0;
  const message = sse.message || task?.progress_msg || '';

  return (
    <>
      <div className="toolbar">
        <span className="crumb">
          <Link to="/tasks" style={{ color: 'inherit', textDecoration: 'none' }}>
            任务
          </Link>{' '}
          <span style={{ color: 'var(--fg-4)' }}>›</span> <b>{taskId}</b>
        </span>
        <div className="spacer" />
        {task?.batch_no && (
          <Link to={`/batches/${encodeURIComponent(task.batch_no)}`} className="btn">
            <I.batches size={13} /> 批次详情
          </Link>
        )}
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: 14 }}>
        {error && (
          <div style={{ padding: 12, background: 'var(--fail-soft)', color: 'var(--fail)', marginBottom: 12 }}>
            {error}
          </div>
        )}
        <div className="chart-card" style={{ margin: 0, marginBottom: 12 }}>
          <div className="chart-head">
            <span className="title">进度</span>
            <span className="axes">{task?.batch_no || '—'}</span>
            <div className="right">
              <span
                className={`badge ${
                  status === 'success'
                    ? 'done'
                    : status === 'failed' || status === 'error'
                    ? 'err'
                    : status === 'running'
                    ? 'run'
                    : 'idle'
                }`}
              >
                {{
                  pending: '排队中',
                  running: '运行中',
                  success: '成功',
                  failed: '失败',
                  error: '错误',
                }[status || 'pending']}
              </span>
            </div>
          </div>
          <div style={{ padding: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
              <div
                style={{
                  flex: 1,
                  height: 8,
                  background: 'var(--bg-panel-2)',
                  border: '1px solid var(--border)',
                  borderRadius: 3,
                  overflow: 'hidden',
                }}
              >
                <div
                  style={{
                    width: `${progress}%`,
                    height: '100%',
                    background:
                      status === 'failed' || status === 'error'
                        ? 'var(--fail)'
                        : status === 'success'
                        ? 'var(--pass)'
                        : 'var(--primary)',
                    transition: 'width 0.3s',
                  }}
                />
              </div>
              <span className="mono" style={{ width: 56, textAlign: 'right' }}>
                {progress}%
              </span>
            </div>
            <div className="mono dim" style={{ fontSize: 12 }}>
              {message || '等待处理中…'}
            </div>
            {(sse.error || task?.error_msg) && (
              <div
                style={{
                  marginTop: 10,
                  padding: 10,
                  background: 'var(--fail-soft)',
                  border: '1px solid var(--fail)',
                  borderRadius: 4,
                  color: 'var(--fail)',
                }}
              >
                <I.alert size={12} /> {sse.error || task?.error_msg}
              </div>
            )}
          </div>
        </div>

        {task && (
          <div className="chart-card" style={{ margin: 0 }}>
            <div className="chart-head">
              <span className="title">详情</span>
            </div>
            <table className="dtable">
              <tbody>
                <tr>
                  <td className="muted">任务 ID</td>
                  <td className="mono">{task.id}</td>
                </tr>
                <tr>
                  <td className="muted">批次</td>
                  <td>
                    <b>{task.batch_no}</b>
                  </td>
                </tr>
                <tr>
                  <td className="muted">开始时间</td>
                  <td className="mono">{task.started_at ? new Date(task.started_at).toLocaleString() : '—'}</td>
                </tr>
                <tr>
                  <td className="muted">结束时间</td>
                  <td className="mono">{task.finished_at ? new Date(task.finished_at).toLocaleString() : '—'}</td>
                </tr>
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
