import type { AnalysisSpec } from '../../../types/platformContracts';
import { jsonCell } from '../model/provenance';
import type { AnalysisDataset } from '../model/dataset';
import { templateSpec } from './projectBackup';
import { parseAnalysisSpec, validateProjectBackup } from './projectCodec';
import { PROJECT_CHUNK_ROWS, ProjectError, type ProjectHead, type ProjectVersion, type ProjectVersionWithRows } from './projectTypes';
import type { ProjectStore, ProjectTemplate } from './ProjectStore';

const DB_NAME = 'aln_analysis_projects';
interface RowChunk { chunkId: string; datasetId: string; index: number; rows: AnalysisDataset['rows']; digest: string }
interface Cached { project: ProjectVersionWithRows; rows: Record<string, AnalysisDataset['rows']> }
function error(value: unknown, fallback = 'IndexedDB不可用'): ProjectError {
  if (value instanceof ProjectError) return value;
  return new ProjectError(value instanceof DOMException && value.name === 'QuotaExceededError'
    ? 'QUOTA' : value instanceof DOMException && value.name === 'VersionError'
      ? 'UNSUPPORTED_VERSION' : 'UNAVAILABLE', value instanceof Error ? value.message : fallback);
}
export function openProjectDb(factory: IDBFactory | undefined = globalThis.indexedDB): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (!factory) { reject(new ProjectError('UNAVAILABLE', '当前环境不支持项目存储')); return; }
    let request: IDBOpenDBRequest;
    let blocked = false;
    try { request = factory.open(DB_NAME, 1); } catch (e) { reject(error(e)); return; }
    request.onupgradeneeded = () => {
      const db = request.result;
      db.createObjectStore('heads', { keyPath: 'projectId' });
      db.createObjectStore('versions', { keyPath: ['projectId', 'versionId'] });
      db.createObjectStore('chunks', { keyPath: 'chunkId' });
      db.createObjectStore('templates', { keyPath: 'templateId' });
    };
    request.onblocked = () => { blocked = true; reject(new ProjectError('UNAVAILABLE', '请关闭其他项目窗口后重试')); };
    request.onerror = () => reject(error(request.error));
    request.onsuccess = () => {
      const db = request.result;
      if (blocked) { db.close(); return; }
      db.onversionchange = () => db.close();
      resolve(db);
    };
  });
}
function done(tx: IDBTransaction, reason: () => unknown = () => tx.error): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onabort = () => reject(error(reason(), '存储事务已中止'));
    tx.onerror = () => { /* Abort owns the rejection, after all requests have settled. */ };
  });
}
function request<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(error(req.error));
  });
}
async function digestRows(rows: AnalysisDataset['rows']) {
  const bytes = new TextEncoder().encode(JSON.stringify(rows.map((row) => jsonCell(row))));
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, '0')).join('');
}
function sameCell(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((cell, i) => sameCell(cell, b[i]));
  if (!a || !b || typeof a !== 'object' || typeof b !== 'object' || Array.isArray(a) || Array.isArray(b)) return false;
  const first = Object.entries(a), second = Object.fromEntries(Object.entries(b));
  return first.length === Object.keys(second).length && first.every(([key, cell]) => key in second && sameCell(cell, second[key]));
}
export function unchangedChunk(previous: AnalysisDataset['rows'], next: AnalysisDataset['rows'], offset: number) {
  const end = Math.min(next.length, offset + PROJECT_CHUNK_ROWS);
  if (Math.min(previous.length, offset + PROJECT_CHUNK_ROWS) !== end) return false;
  for (let i = offset; i < end; i += 1) if (!sameCell(next[i], previous[i])) return false;
  return true;
}

export class IndexedDbProjectStore implements ProjectStore {
  private connection: IDBDatabase | null = null;
  private cached = new Map<string, Cached>();
  constructor(private factory: IDBFactory | undefined = globalThis.indexedDB) {}
  close() { this.connection?.close(); this.connection = null; }
  private async db() {
    if (!this.connection) {
      this.connection = await openProjectDb(this.factory);
      this.connection.onversionchange = () => this.close();
    }
    return this.connection;
  }
  async list(): Promise<ProjectHead[]> {
    const db = await this.db(), tx = db.transaction('heads', 'readonly');
    const completed = done(tx), rows = await request(tx.objectStore('heads').getAll());
    await completed;
    return rows.map((row) => validateHead(row)).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  }
  async load(projectId: string): Promise<ProjectVersionWithRows> {
    const db = await this.db(), tx = db.transaction(['heads', 'versions', 'chunks'], 'readonly');
    let head: unknown, version: unknown;
    const chunks = new Map<string, unknown>();
    let fault: unknown;
    const completed = done(tx, () => fault ?? tx.error);
    const headRequest = tx.objectStore('heads').get(projectId);
    headRequest.onsuccess = () => {
      try {
        head = headRequest.result;
        const checked = validateHead(head);
        const versionRequest = tx.objectStore('versions').get([projectId, checked.currentVersionId]);
        versionRequest.onsuccess = () => {
          try {
            version = versionRequest.result;
            const refs = versionReferences(version);
            for (const chunkId of refs) {
              const get = tx.objectStore('chunks').get(chunkId);
              get.onsuccess = () => chunks.set(chunkId, get.result);
            }
          } catch (e) { fault = e; tx.abort(); }
        };
      } catch (e) { fault = e; tx.abort(); }
    };
    await completed;
    const h = validateHead(head), v = object(version);
    const byId = object(object(v.registryMetadata).byId), refs = object(v.chunkRefs);
    const datasets: Record<string, unknown> = {};
    for (const [id, metadata] of Object.entries(byId)) {
      const rows: unknown[] = [];
      const ids = stringArray(refs[id]);
      for (let index = 0; index < ids.length; index += 1) {
        const chunk = parseChunk(chunks.get(ids[index]));
        if (chunk.datasetId !== id || chunk.chunkId !== ids[index] || chunk.index !== index
            || !chunk.rows.length || chunk.rows.length > PROJECT_CHUNK_ROWS
            || (index < ids.length - 1 && chunk.rows.length !== PROJECT_CHUNK_ROWS)) {
          throw new ProjectError('CORRUPT', '项目行块归属或顺序损坏');
        }
        if (await digestRows(chunk.rows) !== chunk.digest) throw new ProjectError('CORRUPT', '项目行块摘要不一致');
        rows.push(...chunk.rows);
      }
      datasets[id] = { ...object(metadata), rows };
    }
    const project = validateProjectBackup({ format: 'aln-analysis-project', schema_version: 1,
      exported_at: new Date().toISOString(), project: { head: h, version: v, datasets } }).project;
    this.cached.set(projectId, { project, rows: Object.fromEntries(Object.entries(project.datasets).map(([id, d]) => [id, d.rows])) });
    return project;
  }
  async markOpened(projectId: string, versionId: string): Promise<ProjectHead> {
    const db = await this.db(), tx = db.transaction('heads', 'readwrite');
    let fault: unknown = null, opened: ProjectHead | null = null;
    const completed = done(tx, () => fault ?? tx.error);
    const request = tx.objectStore('heads').get(projectId);
    request.onsuccess = () => {
      try {
        const head = validateHead(request.result);
        if (head.currentVersionId !== versionId) throw new ProjectError('CONFLICT', '打开项目时版本已变化');
        opened = { ...head, lastOpenedAt: new Date().toISOString() };
        tx.objectStore('heads').put(opened);
      } catch (failure) { fault = failure; tx.abort(); }
    };
    await completed;
    if (!opened) throw new ProjectError('CORRUPT', '项目未打开');
    const cached = this.cached.get(projectId);
    if (cached) cached.project.head = opened;
    return opened;
  }
  async save(input: ProjectVersionWithRows, expectedGeneration: number | null): Promise<ProjectHead> {
    const db = await this.db();
    let previous = this.cached.get(input.head.projectId);
    if (!previous && expectedGeneration !== null) {
      await this.load(input.head.projectId); previous = this.cached.get(input.head.projectId);
    }
    if ((input.head.kind === 'snapshot' || previous?.project.head.kind === 'snapshot') && expectedGeneration !== null) {
      throw new ProjectError('CONFLICT', '固定快照请先另存为新项目');
    }
    const now = new Date().toISOString(), versionId = crypto.randomUUID();
    const head: ProjectHead = { ...input.head, currentVersionId: versionId,
      generation: (expectedGeneration ?? 0) + 1, updatedAt: now, schemaVersion: 1 };
    // The serializer already captured immutable metadata; draft fields are separate.
    // Yield before preparing storage so editing is not one long serialization task.
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
    const version: ProjectVersion = { ...input.version, projectId: head.projectId,
      versionId, baseGeneration: expectedGeneration ?? 0, status: 'draft', createdAt: now, chunkRefs: {} };
    const newChunks: RowChunk[] = [];
    for (const id of version.registryMetadata.order) {
      const dataset = input.datasets[id];
      if (!dataset) throw new ProjectError('CORRUPT', '工作表缺失');
      const old = previous?.project.datasets[id];
      const oldRows = previous?.rows[id];
      const oldRefs = previous?.project.version.chunkRefs[id];
      const refs: string[] = [];
      for (let offset = 0; offset < dataset.rows.length; offset += PROJECT_CHUNK_ROWS) {
        const index = Math.floor(offset / PROJECT_CHUNK_ROWS);
        if (index > 0 && index % 8 === 0) await new Promise<void>((resolve) => setTimeout(resolve, 0));
        if (old && oldRows && oldRefs?.[index] && (old.revision === dataset.revision
          || unchangedChunk(oldRows, dataset.rows, offset))) { refs.push(oldRefs[index]); continue; }
        const chunkId = crypto.randomUUID(), rows = dataset.rows.slice(offset, offset + PROJECT_CHUNK_ROWS);
        newChunks.push({ chunkId, datasetId: id, index, rows, digest: await digestRows(rows) }); refs.push(chunkId);
      }
      version.chunkRefs[id] = refs;
    }
    const stage = db.transaction(['versions', 'chunks'], 'readwrite');
    const staged = done(stage);
    try {
      stage.objectStore('versions').add(version);
      for (const chunk of newChunks) stage.objectStore('chunks').add(chunk);
    } catch (e) { stage.abort(); await staged.catch(() => {}); throw error(e); }
    await staged;
    const tx = db.transaction(['heads', 'versions', 'chunks'], 'readwrite');
    let fault: unknown, oldHead: ProjectHead | null = null;
    const completed = done(tx, () => fault ?? tx.error);
    let remaining = 1 + Object.values(version.chunkRefs).flat().length;
    const finish = () => {
      remaining -= 1;
      if (remaining !== 0) return;
      if ((expectedGeneration === null && oldHead !== null)
        || (expectedGeneration !== null && oldHead?.generation !== expectedGeneration)) {
        fault = new ProjectError('CONFLICT', '其他窗口已保存新版本，请重载或另存'); tx.abort(); return;
      }
      try {
        version.status = 'complete';
        tx.objectStore('versions').put(version);
        tx.objectStore('heads').put(head);
      } catch (e) { fault = e; try { tx.abort(); } catch { /* Already aborted. */ } }
    };
    const getHead = tx.objectStore('heads').get(head.projectId);
    getHead.onsuccess = () => {
      try { oldHead = getHead.result === undefined ? null : validateHead(getHead.result); finish(); }
      catch (e) { fault = e; tx.abort(); }
    };
    for (const chunkId of Object.values(version.chunkRefs).flat()) {
      const get = tx.objectStore('chunks').get(chunkId);
      get.onsuccess = () => {
        if (!get.result) { fault = new ProjectError('CORRUPT', '保存期间行块已被清理，请重试'); tx.abort(); }
        else finish();
      };
    }
    await completed;
    const project: ProjectVersionWithRows = { head, version, datasets: input.datasets };
    this.cached.set(head.projectId, { project,
      rows: Object.fromEntries(Object.entries(input.datasets).map(([id, d]) => [id, d.rows])) });
    return head;
  }
  async remove(projectId: string, expectedGeneration: number): Promise<void> {
    const db = await this.db(), tx = db.transaction(['heads', 'versions'], 'readwrite');
    let fault: unknown;
    const completed = done(tx, () => fault ?? tx.error), get = tx.objectStore('heads').get(projectId);
    get.onsuccess = () => {
      if (!get.result || validateHead(get.result).generation !== expectedGeneration) {
        fault = new ProjectError('CONFLICT', '项目版本已变化'); tx.abort(); return;
      }
      tx.objectStore('heads').delete(projectId);
      tx.objectStore('versions').delete(IDBKeyRange.bound([projectId, ''], [projectId, '\uffff']));
    };
    await completed; this.cached.delete(projectId);
  }
  async saveTemplate(spec: AnalysisSpec, name: string): Promise<ProjectTemplate> {
    const clean = parseAnalysisSpec(templateSpec(spec));
    const row: ProjectTemplate = { templateId: crypto.randomUUID(), name, spec: clean };
    const db = await this.db(), tx = db.transaction('templates', 'readwrite');
    const completed = done(tx); tx.objectStore('templates').add(row); await completed; return row;
  }
  async listTemplates(): Promise<ProjectTemplate[]> {
    const db = await this.db(), tx = db.transaction('templates', 'readonly');
    const completed = done(tx), rows = await request(tx.objectStore('templates').getAll()); await completed;
    return rows.map((row) => {
      const value = object(row); return { templateId: text(value.templateId), name: text(value.name), spec: parseAnalysisSpec(value.spec) };
    });
  }
  async collectOrphans(): Promise<void> {
    const db = await this.db(), tx = db.transaction(['heads', 'versions', 'chunks'], 'readwrite');
    const completed = done(tx);
    const headsRequest = tx.objectStore('heads').getAll(), versionsRequest = tx.objectStore('versions').getAll();
    let heads: ProjectHead[] | null = null, versions: unknown[] | null = null;
    const collect = () => {
      if (!heads || !versions) return;
      const keep = new Set<string>();
      for (const head of heads) {
        keep.add(JSON.stringify([head.projectId, head.currentVersionId]));
        const prior = versions.map(object).filter((v) => v.projectId === head.projectId && v.status === 'complete'
          && v.versionId !== head.currentVersionId).sort((a, b) => num(b.baseGeneration) - num(a.baseGeneration))[0];
        if (prior) keep.add(JSON.stringify([prior.projectId, prior.versionId]));
      }
      const referenced = new Set<string>();
      for (const raw of versions) {
        const v = object(raw), key = [text(v.projectId), text(v.versionId)];
        if (keep.has(JSON.stringify(key))) versionReferences(v).forEach((ref) => referenced.add(ref));
        else tx.objectStore('versions').delete(key);
      }
      const chunks = tx.objectStore('chunks').openKeyCursor();
      chunks.onsuccess = () => {
        const cursor = chunks.result;
        if (!cursor) return;
        if (typeof cursor.primaryKey === 'string' && !referenced.has(cursor.primaryKey)) tx.objectStore('chunks').delete(cursor.primaryKey);
        cursor.continue();
      };
    };
    headsRequest.onsuccess = () => { heads = headsRequest.result.map(validateHead); collect(); };
    versionsRequest.onsuccess = () => { versions = versionsRequest.result; collect(); };
    await completed;
  }
}

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new ProjectError('CORRUPT', '存储对象损坏');
  return Object.fromEntries(Object.entries(value));
}
function text(value: unknown): string { if (typeof value !== 'string') throw new ProjectError('CORRUPT', '字符串损坏'); return value; }
function num(value: unknown): number { if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) throw new ProjectError('CORRUPT', '版本数字损坏'); return value; }
function stringArray(value: unknown): string[] { if (!Array.isArray(value)) throw new ProjectError('CORRUPT', '块引用损坏'); return value.map(text); }
function versionReferences(value: unknown) { return Object.values(object(object(value).chunkRefs)).flatMap(stringArray); }
function validateHead(value: unknown): ProjectHead {
  const h = object(value);
  if (h.schemaVersion !== 1) throw new ProjectError('UNSUPPORTED_VERSION', '项目数据库版本不支持');
  if (!['draft', 'named', 'snapshot'].includes(text(h.kind))) throw new ProjectError('CORRUPT', '项目类型损坏');
  const kind = h.kind === 'draft' ? 'draft' : h.kind === 'named' ? 'named' : 'snapshot';
  return { projectId: text(h.projectId), currentVersionId: text(h.currentVersionId), generation: num(h.generation),
    name: text(h.name), kind, updatedAt: text(h.updatedAt), lastOpenedAt: text(h.lastOpenedAt), schemaVersion: 1 };
}
function parseChunk(value: unknown): RowChunk {
  const v = object(value);
  if (!Array.isArray(v.rows)) throw new ProjectError('CORRUPT', '行块缺失');
  const rows = v.rows.map((raw) => {
    const row = object(raw);
    if (typeof row.structureType !== 'string' || (row.areaReality !== null && typeof row.areaReality !== 'number')) throw new ProjectError('CORRUPT', '行字段损坏');
    return { ...row, rowId: text(row.rowId), __rowId: text(row.__rowId), datasetId: text(row.datasetId),
      structureType: row.structureType, areaReality: typeof row.areaReality === 'number' ? row.areaReality : null };
  });
  return { chunkId: text(v.chunkId), datasetId: text(v.datasetId), index: num(v.index), rows, digest: text(v.digest) };
}
