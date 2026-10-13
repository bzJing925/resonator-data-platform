import React from 'react';
import type { useProjectAutosave } from './useProjectAutosave';

export default function ProjectSaveBadge({ project }: { project: ReturnType<typeof useProjectAutosave> }) {
  const labels = { idle: '尚未保存', dirty: '未保存', saving: '正在保存', saved: '已保存', error: '保存失败', conflict: '保存冲突' };
  return <div role="status" data-testid="project-save-status" style={{ color: project.error ? '#b42318' : undefined }}>
    <span>{project.head?.kind === 'snapshot' ? '固定快照（只读，编辑请另存） · ' : ''}{project.sessionMode ? '会话模式：' : ''}{project.head?.name ?? '未命名项目'} · {labels[project.status]}</span>
    {project.lastSavedAt && <span> · 最后保存 {new Date(project.lastSavedAt).toLocaleTimeString()}</span>}
    {project.dirtySince && <span> · 未保存始于 {new Date(project.dirtySince).toLocaleTimeString()}</span>}
    {project.error && <span> · {project.error.code}：{project.error.message}</span>}
    {project.status === 'conflict' && <>
      <button onClick={() => { void project.reload().catch(() => {}); }}>重载其他窗口版本</button>
      <button onClick={() => { void project.saveAs().catch(() => {}); }}>另存为新项目</button>
    </>}
    {project.status === 'error' && <button onClick={() => { void project.retry().catch(() => {}); }}>重试保存</button>}
    <small> · 重启后会话撤销历史清空</small>
  </div>;
}
