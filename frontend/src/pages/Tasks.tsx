import React, { memo, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import I from '../components/Icons';
import { listTasks } from '../api/endpoints';
import type { Task } from '../types';

interface BadgeProps {
  status?: string;
}

const Badge = memo(function Badge({ status }: BadgeProps) {
  const map: Record<string, [string, string]> = {
    running: ['run', '运行中'],
    success: ['done', '成功'],
    failed: ['err', '失败'],
    error: ['err', '错误'],
    pending: ['idle', '排队中'],
  };
  const [cls, txt] = map[status || ''] || ['idle', String(status || '').toUpperCase()];
  return <span className={`badge ${cls}`}>{txt}</span>;
});

interface TaskRowProps {
  task: Task;
}

const TaskRow = memo(function TaskRow({ task }: TaskRowProps) {
  const t = task;
  return (
    <tr>
      <td className="mono">{String(t.id).slice(0, 12)}</td>
      <td>
        <b style={{ color: 'var(--fg-1)' }}>{t.batch_no}</b>
      </td>
      <td>
        <Badge status={t.status} />
      </td>
      <td>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div
            style={{
              flex: 1,
              height: 5,
              background: 'var(--bg-panel-2)',
              border: '1px solid var(--border)',
              borderRadius: 3,
              overflow: 'hidden',
            }}
          >
            <div
              style={{
                width: `${t.progress_pct || 0}%`,
                height: '100%',
                background:
                  t.status === 'failed' || t.status === 'error'
                    ? 'var(--fail)'
                    : t.status === 'success'
                    ? 'var(--pass)'
                    : 'var(--primary)',
              }}
            />
          </div>
          <span style={{ width: 40, textAlign: 'right' }}>{t.progress_pct || 0}%</span>
        </div>
      </td>
      <td className="mono dim" style={{ fontSize: 11 }}>
        {t.started_at ? new Date(t.started_at).toLocaleString() : '—'}
      </td>
      <td className="mono dim" style={{ fontSize: 11 }}>
        {t.finished_at ? new Date(t.finished_at).toLocaleString() : '—'}
      </td>
      <td>
        <Link to={`/tasks/${t.id}`} className="btn ghost sm" style={{ textDecoration: 'none' }}>
          详情 ›
        </Link>
      </td>
    </tr>
  );
});

export default function Tasks() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // cancelled 让组件卸载后或 effect 重跑时丢弃 in-flight 响应，
    // 避免 setState on unmounted（React 18 不警告但仍是浪费）。
    let cancelled = false;
    const tick = () =>
      listTasks()
        .then((d) => {
          if (cancelled) return;
          setTasks(Array.isArray(d) ? d : (d as { items?: Task[] })?.items || []);
        })
        .catch((e: Error) => { if (!cancelled) setError(e.message); });
    tick();
    const id = setInterval(tick, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <>
      <div className="toolbar">
        <span className="crumb">
          <b>任务</b>
        </span>
        <div className="spacer" />
        <span className="dim mono" style={{ fontSize: 11 }}>
          每 5 秒自动刷新
        </span>
      </div>
      <div style={{ flex: 1, overflow: 'auto' }}>
        {error && (
          <div style={{ padding: 12, background: 'var(--fail-soft)', color: 'var(--fail)' }}>{error}</div>
        )}
        <table className="dtable" style={{ background: 'var(--bg-panel)' }}>
          <thead>
            <tr>
              <th>任务 ID</th>
              <th>批次</th>
              <th>状态</th>
              <th style={{ width: 240 }}>进度</th>
              <th>开始</th>
              <th>结束</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {tasks.length === 0 && (
              <tr>
                <td colSpan={7} className="dim" style={{ textAlign: 'center', padding: 24 }}>
                  暂无任务
                </td>
              </tr>
            )}
            {tasks.map((t) => (
              <TaskRow key={t.id} task={t} />
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
