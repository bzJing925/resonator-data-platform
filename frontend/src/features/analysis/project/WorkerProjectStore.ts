import type { AnalysisSpec } from '../../../types/platformContracts';
import type { WorkspaceState } from '../workspace';
import type { ProjectStore, ProjectTemplate } from './ProjectStore';
import { ProjectError, type ProjectHead, type ProjectVersionWithRows } from './projectTypes';
import { ProjectValueReader, sendProjectValue } from './projectWire';
interface Pending { resolve(value: unknown): void; reject(error: ProjectError): void; reader: ProjectValueReader }
export class WorkerProjectStore implements ProjectStore {
  private worker: Worker | null = null;
  private pending = new Map<string, Pending>();
  private getWorker() {
    if (!this.worker) {
      this.worker = new Worker(new URL('./projectStorageWorker.ts', import.meta.url), { type: 'module' });
      this.worker.onmessage = ({ data }) => {
        const pending = this.pending.get(data.id);
        if (!pending) return;
        try {
          if (data.type === 'chunk') pending.reader.append(data.buffer);
          else if (data.type === 'end' || data.type === 'error') {
            this.pending.delete(data.id);
            if (data.type === 'end') pending.resolve(pending.reader.value);
            else pending.reject(new ProjectError(data.code, data.message));
          }
        } catch { this.pending.delete(data.id); pending.reject(new ProjectError('CORRUPT', '项目传输损坏')); }
      };
      this.worker.onerror = () => this.close();
    }
    return this.worker;
  }
  private async call<T>(method: string, ...args: unknown[]): Promise<T> {
    let worker: Worker;
    try { worker = this.getWorker(); } catch { throw new ProjectError('UNAVAILABLE', '项目存储Worker不可用'); }
    const id = crypto.randomUUID();
    const result = new Promise<unknown>((resolve, reject) => this.pending.set(id, { resolve, reject, reader: new ProjectValueReader() }));
    void result.catch(() => {}); // Close/failure can arrive while bounded encoding yields.
    try {
      worker.postMessage({ id, type: 'begin' });
      await sendProjectValue({ method, args }, (buffer) => { if (this.worker !== worker) throw new Error('Worker closed'); worker.postMessage({ id, type: 'chunk', buffer }, [buffer]); });
      worker.postMessage({ id, type: 'end' });
    } catch { this.pending.get(id)?.reject(new ProjectError('UNAVAILABLE', '项目传输失败')); this.pending.delete(id); }
    return await result as T;
  }
  close() {
    this.worker?.terminate(); this.worker = null;
    for (const pending of this.pending.values()) pending.reject(new ProjectError('UNAVAILABLE', '项目存储Worker已关闭'));
    this.pending.clear();
  }
  list() { return this.call<ProjectHead[]>('list'); }
  load(id: string) { return this.call<ProjectVersionWithRows>('load', id); }
  saveWorkspace(state: Pick<WorkspaceState, 'registry' | 'pointStyles' | 'activeTabId' | 'spec' | 'notes' | 'projectExtras'>, head: ProjectHead, generation: number | null) {
    const { registry, pointStyles, activeTabId, spec, notes, projectExtras } = state;
    return this.call<ProjectHead>('saveWorkspace', { registry, pointStyles, activeTabId, spec, notes, projectExtras }, head, generation);
  }
  save(input: ProjectVersionWithRows, generation: number | null) { return this.call<ProjectHead>('save', input, generation); }
  remove(id: string, generation: number) { return this.call<void>('remove', id, generation); }
  saveTemplate(spec: AnalysisSpec, name: string) { return this.call<ProjectTemplate>('saveTemplate', spec, name); }
  listTemplates() { return this.call<ProjectTemplate[]>('listTemplates'); }
  markOpened(id: string, version: string) { return this.call<ProjectHead>('markOpened', id, version); }
  collectOrphans() { return this.call<void>('collectOrphans'); }
}
