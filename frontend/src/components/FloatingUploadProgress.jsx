import React, { useEffect } from 'react';
import I from './Icons.jsx';
import useSSE from '../hooks/useSSE.js';
import { useUploadProgress } from '../contexts/UploadProgressContext.jsx';

function TaskItem({ task }) {
  const { removeTask, updateTask } = useUploadProgress();
  const sse = useSSE(task.task_id, { enabled: !task.done });

  useEffect(() => {
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
  }, [sse.event, sse.done, sse.status, sse.progress, sse.stage, sse.stageProgress, sse.message, sse.error]);

  const isError = task.status === 'error' || sse.status === 'error';
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
            {isSuccess ? '成功' : isError ? '失败' : stageLabel || '运行中'}
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

      {task.message && (
        <div className="mono dim" style={{ fontSize: 10, marginTop: 3, color: isError ? 'var(--fail)' : undefined }}>
          {task.message}
        </div>
      )}
    </div>
  );
}

export default function FloatingUploadProgress() {
  const { tasks, removeTask } = useUploadProgress();

  if (tasks.length === 0) return null;

  // 自动移除已完成的任务（保留 30 秒，方便用户看到成功/失败）
  const visibleTasks = tasks.filter((t) => {
    if (!t.done) return true;
    return Date.now() - (t.addedAt || 0) < 30000;
  });

  if (visibleTasks.length === 0) {
    // 全部过期，批量清理
    tasks.filter((t) => t.done).forEach((t) => removeTask(t.task_id));
    return null;
  }

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
        <span style={{ fontSize: 12, fontWeight: 600 }}>上传任务 ({tasks.length})</span>
        <button className="btn ghost sm" style={{ padding: '2px 6px' }} onClick={() => tasks.forEach((t) => removeTask(t.task_id))}>
          全部关闭
        </button>
      </div>
      <div style={{ overflow: 'auto' }}>
        {tasks.map((t) => (
          <TaskItem key={t.task_id} task={t} />
        ))}
      </div>
    </div>
  );
}
