import React, { useEffect } from 'react';
import { Link } from 'react-router-dom';
import I from './Icons';
import { taskLabel } from '../tasks/taskStatus';
import useSSE from '../hooks/useSSE';
import { useUploadProgress } from '../contexts/UploadProgressContext';

interface TaskItemProps {
  task: {
    task_id: string | number;
    batch_no?: string;
    done?: boolean;
    status?: string;
    progress?: number;
    stage?: string;
    stageProgress?: number;
    message?: string;
  };
}

function TaskItem({ task }: TaskItemProps) {
  const { removeTask, updateTask } = useUploadProgress();
  const sse = useSSE(task.task_id, { enabled: !task.done });

  useEffect(() => {
    if (task.done) return;
    if (sse.done || sse.status === 'success' || sse.status === 'error') {
      updateTask(task.task_id, {
        status: sse.status,
        progress: sse.progress,
        stage: sse.stage,
        stageProgress: sse.stageProgress,
        message: sse.message || sse.error,
        done: true,
      });
    } else {
      updateTask(task.task_id, {
        status: sse.status,
        progress: sse.progress,
        stage: sse.stage,
        stageProgress: sse.stageProgress,
        message: sse.message,
      });
    }
  }, [task.done, sse.event, sse.done, sse.status, sse.progress, sse.stage, sse.stageProgress, sse.message, sse.error]);

  const isError = ['error', 'failed'].includes(task.status ?? '') || sse.status === 'failed';
  const isSuccess = task.status === 'success' || sse.status === 'success';

  const stageLabel =
    task.stage === 'metrics' ? '指标计算' :
    task.stage === 'extract' ? '解压' :
    task.stage === 'done' ? '完成' :
    task.stage === 'failed' ? '失败' : '';

  return (
    <div style={{ padding: '8px 10px', borderBottom: '1px solid var(--border)', fontSize: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
        <span style={{ fontWeight: 600, color: 'var(--fg-1)' }}>{task.batch_no}</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span
            className={`badge ${isSuccess ? 'done' : isError ? 'err' : 'run'}`}
            style={{ fontSize: 10 }}
          >
            {taskLabel(task.status ?? sse.status)}
          </span>
          <button
            className="btn ghost sm"
            style={{ padding: '2px 4px' }}
            onClick={() => removeTask(task.task_id)}
            title="关闭"
          >
            <I.x size={10} />
          </button>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <div
          style={{
            flex: 1,
            height: 5,
            background: 'var(--bg-panel-2)',
            border: '1px solid var(--border)',
            borderRadius: 2,
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              width: `${task.progress || 0}%`,
              height: '100%',
              background: isError ? 'var(--fail)' : isSuccess ? 'var(--pass)' : 'var(--primary)',
              transition: 'width 0.3s',
            }}
          />
        </div>
        <span className="mono" style={{ fontSize: 10, width: 36, textAlign: 'right' }}>
          {task.progress || 0}%
        </span>
      </div>

      {task.stage && task.stage !== 'done' && task.stage !== 'failed' && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 10, color: 'var(--fg-3)', width: 50 }}>{stageLabel}</span>
          <div
            style={{
              flex: 1,
              height: 4,
              background: 'var(--bg-panel-2)',
              borderRadius: 2,
              overflow: 'hidden',
            }}
          >
            <div
              style={{
                width: `${task.stageProgress || 0}%`,
                height: '100%',
                background: isError ? 'var(--fail)' : isSuccess ? 'var(--pass)' : 'var(--primary)',
                opacity: 0.7,
                transition: 'width 0.3s',
              }}
            />
          </div>
        </div>
      )}

      <Link to={`/tasks/${task.task_id}`}>任务详情与停止/重试</Link>
      {task.message && (
        <div className="mono dim" style={{ fontSize: 10, marginTop: 3, color: isError ? 'var(--fail)' : undefined }}>
          {task.message}
        </div>
      )}
    </div>
  );
}

interface UploadTask {
  task_id: string | number;
  done?: boolean;
  addedAt?: number;
  completedAt?: number;
  group_id?: string;
}

export default function FloatingUploadProgress() {
  const { tasks, removeTask } = useUploadProgress();

  // 自动移除已完成的任务（完成后保留 30 秒，方便用户看到成功/失败）
  useEffect(() => {
    const timers = tasks.filter((t: UploadTask) => t.done).map((t: UploadTask) => {
      const elapsed = Date.now() - (t.completedAt ?? t.addedAt ?? Date.now());
      return setTimeout(() => removeTask(t.task_id), Math.max(0, 30000 - elapsed));
    });
    return () => timers.forEach(clearTimeout);
  }, [tasks, removeTask]);

  const visibleTasks = tasks.filter((t: UploadTask) => {
    if (!t.done) return true;
    return Date.now() - (t.completedAt ?? t.addedAt ?? 0) < 30000;
  });

  if (visibleTasks.length === 0) return null;

  return (
    <div
      style={{
        position: 'fixed',
        right: 18,
        bottom: 18,
        width: 340,
        maxHeight: 360,
        background: 'var(--bg-panel)',
        border: '1px solid var(--border)',
        borderRadius: 6,
        boxShadow: '0 8px 24px rgba(0,0,0,0.18)',
        zIndex: 1000,
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          padding: '8px 10px',
          borderBottom: '1px solid var(--border)',
          background: 'var(--bg-panel-2)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <span style={{ fontSize: 12, fontWeight: 600 }}>上传任务 ({visibleTasks.length})</span>
        <button className="btn ghost sm" style={{ padding: '2px 6px' }} onClick={() => visibleTasks.forEach((t: UploadTask) => removeTask(t.task_id))}>
          全部关闭
        </button>
      </div>
      <div style={{ overflow: 'auto' }}>
        {[...new Set(visibleTasks.map((task: UploadTask) => task.group_id).filter(Boolean))].map((groupId: string) => {
          const group = visibleTasks.filter((task: UploadTask) => task.group_id === groupId);
          return <p key={groupId} style={{ padding: '0 10px', fontSize: 12 }}><Link to={`/upload?groupId=${encodeURIComponent(groupId)}`}>上传组详情</Link> · {group.filter((task: UploadTask) => task.done).length}/{group.length} 已结束</p>;
        })}
        {visibleTasks.map((t: UploadTask) => (
          <TaskItem key={t.task_id} task={t as TaskItemProps['task']} />
        ))}
      </div>
    </div>
  );
}
