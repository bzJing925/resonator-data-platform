import { useCallback, useEffect, useRef, useState } from 'react';
import type { WorkspaceState } from '../workspace';
import { WorkerProjectStore } from './WorkerProjectStore';
import { ProjectError, type ProjectHead, type ProjectVersionWithRows } from './projectTypes';

export type SaveStatus = 'idle' | 'dirty' | 'saving' | 'saved' | 'error' | 'conflict';
const CURRENT_PROJECT = 'aln_analysis_current_project';
export const MIGRATED_PROJECT = 'aln_analysis_migrated_v1';
function currentId() { try { return localStorage.getItem(CURRENT_PROJECT); } catch { return null; } }
function equal(a: WorkspaceState | null, b: WorkspaceState) {
  return a && a.registry === b.registry && a.spec === b.spec && a.pointStyles === b.pointStyles
    && a.activeTabId === b.activeTabId && a.notes === b.notes && a.projectExtras === b.projectExtras;
}
function draftHead(): ProjectHead {
  const now = new Date().toISOString();
  return { projectId: crypto.randomUUID(), currentVersionId: crypto.randomUUID(), generation: 1,
    name: '未命名项目', kind: 'draft', updatedAt: now, lastOpenedAt: now, schemaVersion: 1 };
}
export function useProjectAutosave(state: WorkspaceState,
  restore: (project: ProjectVersionWithRows) => WorkspaceState) {
  const [store] = useState(() => new WorkerProjectStore());
  const [head, setHead] = useState<ProjectHead | null>(null);
  const [loading, setLoading] = useState(true), [sessionMode, setSessionMode] = useState(false);
  const [status, setStatus] = useState<SaveStatus>('idle');
  const [error, setError] = useState<ProjectError | null>(null);
  const [lastSavedAt, setLastSavedAt] = useState<string | null>(null);
  const [dirtySince, setDirtySince] = useState<string | null>(null);
  const latest = useRef(state), saved = useRef<WorkspaceState | null>(null);
  const current = useRef<ProjectHead | null>(null), generation = useRef<number | null>(null);
  const saving = useRef<Promise<ProjectHead> | null>(null), timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const paused = useRef(false), mounted = useRef(true), initialized = useRef(false);
  latest.current = state;
  useEffect(() => {
    mounted.current = true;
    if (!initialized.current) {
      initialized.current = true;
      void (async () => {
        try {
          const heads = await store.list(), id = currentId();
          const chosen = heads.find((h) => h.projectId === id) ?? heads[0];
          if (chosen) {
            const project = await store.load(chosen.projectId);
            project.head = await store.markOpened(project.head.projectId, project.head.currentVersionId);
            if (!mounted.current) return;
            saved.current = restore(project); current.current = project.head;
            generation.current = project.head.generation; setHead(project.head);
            setLastSavedAt(project.head.updatedAt); setStatus('saved');
          } else { current.current = draftHead(); setHead(current.current); setStatus('dirty'); }
        } catch (e) {
          if (!mounted.current) return;
          const failure = e instanceof ProjectError ? e : new ProjectError('UNAVAILABLE', '项目存储无法读取');
          setError(failure); setSessionMode(true); paused.current = true; setStatus('error');
        } finally { if (mounted.current) setLoading(false); }
      })();
    }
    return () => { mounted.current = false; if (timer.current) clearTimeout(timer.current); };
  }, [restore, store]);
  const persist = useCallback(async (destination: ProjectHead, expected: number | null) => {
    const captured = latest.current;
    const promise = store.saveWorkspace(captured, destination, expected);
    saving.current = promise; setStatus('saving'); setError(null);
    try {
      const completed = await promise;
      current.current = completed; generation.current = completed.generation; saved.current = captured;
      try { localStorage.setItem(CURRENT_PROJECT, completed.projectId); localStorage.setItem(MIGRATED_PROJECT, '1'); } catch { /* IDB commit remains valid. */ }
      if (mounted.current) {
        setHead(completed); setLastSavedAt(completed.updatedAt);
        const clean = equal(captured, latest.current);
        setStatus(clean ? 'saved' : 'dirty'); if (clean) setDirtySince(null);
      }
      return completed;
    } catch (e) {
      const failure = e instanceof ProjectError ? e : new ProjectError('UNAVAILABLE', '保存失败');
      paused.current = true;
      if (mounted.current) { setError(failure); setStatus(failure.code === 'CONFLICT' ? 'conflict' : 'error'); }
      throw failure;
    } finally { saving.current = null; }
  }, [store]);
  const saveNow = useCallback(async () => {
    if (saving.current) await saving.current;
    if (sessionMode || !current.current) throw new ProjectError('UNAVAILABLE', '当前为未持久化会话');
    if (paused.current) throw error ?? new ProjectError('CONFLICT', '请重载或另存');
    if (equal(saved.current, latest.current)) return current.current;
    return persist(current.current, generation.current);
  }, [persist, sessionMode, error]);
  useEffect(() => {
    if (loading || state.readOnly || paused.current || equal(saved.current, state)) return;
    if (!saving.current) setStatus('dirty');
    setDirtySince((time) => time ?? new Date().toISOString());
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => { void saveNow().catch(() => {}); }, 1000);
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [loading, state.registry, state.spec, state.pointStyles, state.activeTabId, state.notes, state.projectExtras, state.readOnly, saveNow, status]);
  useEffect(() => {
    const onClose = (event: BeforeUnloadEvent) => {
      if (!latest.current.readOnly && !equal(saved.current, latest.current)) { event.preventDefault(); event.returnValue = ''; }
    };
    window.addEventListener('beforeunload', onClose);
    return () => window.removeEventListener('beforeunload', onClose);
  }, []);
  const reload = useCallback(async (projectId = current.current?.projectId) => {
    if (!projectId) throw new ProjectError('UNAVAILABLE', '无已保存项目');
    if (timer.current) clearTimeout(timer.current);
    if (saving.current) await saving.current.catch(() => {});
    const project = await store.load(projectId);
    project.head = await store.markOpened(project.head.projectId, project.head.currentVersionId);
    saved.current = restore(project); current.current = project.head; generation.current = project.head.generation;
    paused.current = false; setSessionMode(false); setError(null); setHead(project.head);
    setStatus('saved'); setDirtySince(null); setLastSavedAt(project.head.updatedAt);
    try { localStorage.setItem(CURRENT_PROJECT, projectId); } catch { /* Preference only. */ }
    return project;
  }, [restore, store]);
  const saveAs = useCallback(async (name = '未命名项目副本') => {
    if (saving.current) await saving.current.catch(() => {});
    const destination = { ...draftHead(), kind: 'named' as const, name };
    paused.current = false; setSessionMode(false);
    return persist(destination, null);
  }, [persist]);
  const retry = useCallback(async () => {
    if (error?.code === 'CONFLICT') throw error;
    paused.current = false; setSessionMode(false); setError(null);
    if (!current.current) current.current = draftHead();
    return persist(current.current, generation.current);
  }, [error, persist]);
  const rename = useCallback(async (name: string) => {
    if (paused.current) throw error ?? new ProjectError('CONFLICT', '请重载或另存');
    if (!current.current || latest.current.readOnly) throw new ProjectError('CONFLICT', '固定快照请另存为新项目');
    if (saving.current) await saving.current;
    return persist({ ...current.current, name, kind: 'named' }, generation.current);
  }, [error, persist]);
  return { store, head, loading, status, error, sessionMode, lastSavedAt, dirtySince, rename,
    saveNow, reload, saveAs, retry };
}
