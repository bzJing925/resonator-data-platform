import React, { useCallback, useRef, useState } from 'react';
import { useAnalysisWorkspace } from '../workspace';
import { migrateLegacyExplore, serializeWorkspace } from './projectCodec';
import { emptyProject, exportProjectBackup, forkProject, readProjectFile, validateTemplateFields } from './projectBackup';
import type { ProjectHead } from './projectTypes';
import type { ProjectTemplate } from './ProjectStore';

export default function ProjectMenu({ fields = null }: { fields?: { name: string; unit?: string }[] | null }) {
  const { project, state } = useAnalysisWorkspace();
  const [opened, setOpened] = useState(false), [name, setName] = useState('');
  const [heads, setHeads] = useState<ProjectHead[]>([]), [templates, setTemplates] = useState<ProjectTemplate[]>([]);
  const [selected, setSelected] = useState(''), [selectedTemplate, setSelectedTemplate] = useState('');
  const [message, setMessage] = useState(''), [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<(() => Promise<void>) | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const run = useCallback(async (action: () => Promise<void>) => {
    setBusy(true); setMessage('');
    try { await action(); setHeads(await project.store.list()); setTemplates(await project.store.listTemplates()); }
    catch (e) { setMessage(e instanceof Error ? e.message : '项目操作失败，当前工作表保留'); }
    finally { setBusy(false); }
  }, [project.store]);
  const requestSwitch = (action: () => Promise<void>) => {
    if (project.status === 'dirty' || project.status === 'saving' || project.status === 'error' || project.status === 'conflict') {
      setPending(() => action); return;
    }
    void run(action);
  };
  const create = async () => {
    const head = await project.store.save(emptyProject(migrateLegacyExplore({})), null);
    await project.reload(head.projectId);
  };
  const exportBackup = async () => {
    let projectData;
    if (project.sessionMode) {
      const head = emptyProject(state.spec).head;
      projectData = serializeWorkspace(state, state.spec, head, state.notes);
      projectData.version.extras = { ...state.projectExtras, persistence: 'session_file_backup' };
    } else {
      if (project.status !== 'saved' || !project.head) throw new Error('请先保存当前修改再导出JSON');
      projectData = await project.store.load(project.head.projectId);
    }
    const text = exportProjectBackup(projectData), url = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
    const anchor = document.createElement('a');
    anchor.href = url; anchor.download = `${projectData.head.name}.aln-project.json`; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
    if (project.sessionMode) setMessage('会话文件备份已下载；浏览器内仍未保存。');
  };
  return <>
    <button data-testid="project-menu" onClick={() => {
      setOpened(!opened); if (!opened) { setName(project.head?.name ?? ''); void run(async () => {}); }
    }}>项目</button>
    {opened && <div className="modal-backdrop" role="presentation">
      <section role="dialog" aria-label="分析项目" className="modal" style={{ padding: 20, maxWidth: 720 }}>
        <h3>分析项目</h3>
        <p>备份保存表格、配置与来源；不包含原始曲线、凭据和会话撤销历史。</p>
        {state.readOnly && <p>当前为固定快照，只读。请另存为新项目后编辑。</p>}
        <label>项目名称 <input aria-label="项目名称" value={name} onChange={(e) => setName(e.target.value)} /></label>
        <div className="group">
          <button disabled={busy} onClick={() => requestSwitch(create)}>新建</button>
          <button disabled={busy || state.readOnly || !name.trim()} onClick={() => void run(async () => { await project.rename(name.trim()); })}>重命名</button>
          <button disabled={busy || !name.trim()} onClick={() => void run(async () => {
            const head = await project.saveAs(name.trim()); await project.reload(head.projectId);
          })}>另存为</button>
          <button disabled={busy || state.readOnly || project.sessionMode} onClick={() => void run(async () => { await project.saveNow(); })}>保存</button>
        </div>
        <label>已保存项目 <select aria-label="已保存项目" value={selected} onChange={(e) => setSelected(e.target.value)}>
          <option value="">选择项目</option>{heads.map((head) => <option key={head.projectId} value={head.projectId}>{head.name} · {head.kind === 'snapshot' ? '固定快照' : '项目'}</option>)}
        </select></label>
        <button disabled={busy || !selected} onClick={() => requestSwitch(async () => { await project.reload(selected); })}>打开</button>
        <div className="group">
          <button disabled={busy || !name.trim()} onClick={() => void run(async () => { await project.store.saveTemplate(state.spec, name.trim()); })}>保存模板</button>
          <select aria-label="分析模板" value={selectedTemplate} onChange={(e) => setSelectedTemplate(e.target.value)}>
            <option value="">选择模板</option>{templates.map((template) => <option key={template.templateId} value={template.templateId}>{template.name}</option>)}
          </select>
          <button disabled={busy || !selectedTemplate} onClick={() => requestSwitch(async () => {
            const template = templates.find((item) => item.templateId === selectedTemplate);
            if (!template) throw new Error('模板已不存在');
            validateTemplateFields(template.spec, fields);
            const head = await project.store.save(emptyProject(template.spec), null); await project.reload(head.projectId);
          })}>应用模板</button>
          <button disabled={busy || project.status !== 'saved' || !project.head || !name.trim()} onClick={() => void run(async () => {
            if (!project.head) return;
            const complete = await project.store.load(project.head.projectId);
            const head = await project.store.save(forkProject(complete, `${name.trim()}快照`, 'snapshot'), null);
            await project.reload(head.projectId);
          })}>固定快照</button>
        </div>
        <div className="group">
          <button disabled={busy} onClick={() => input.current?.click()}>导入JSON</button>
          <button disabled={busy || (!project.sessionMode && project.status !== 'saved')} onClick={() => void run(exportBackup)}>导出JSON</button>
          <input ref={input} type="file" accept="application/json,.json" aria-label="导入项目JSON" hidden onChange={(event) => {
            const file = event.target.files?.[0]; event.target.value = '';
            if (!file) return;
            // Validate the full file before asking to leave the current project.
            void readProjectFile(file).then((validated) => requestSwitch(async () => {
              const head = await project.store.save(validated, null); await project.reload(head.projectId);
            })).catch((e) => setMessage(e instanceof Error ? e.message : '项目文件无效'));
          }} />
        </div>
        {pending && <div role="alertdialog" aria-label="当前项目有未保存修改">
          <p>当前项目有未保存修改。选择保存、放弃本次修改或取消切换。</p>
          <button disabled={busy || project.sessionMode || project.status === 'conflict'} onClick={() => {
            const next = pending; setPending(null); void run(async () => { await project.saveNow(); await next(); });
          }}>保存后继续</button>
          <button disabled={busy} onClick={() => { const next = pending; setPending(null); void run(next); }}>放弃修改并继续</button>
          <button disabled={busy} onClick={() => setPending(null)}>取消切换</button>
        </div>}
        {message && <p role="alert">{message}</p>}
        <button onClick={() => setOpened(false)}>关闭项目菜单</button>
      </section>
    </div>}
  </>;
}
