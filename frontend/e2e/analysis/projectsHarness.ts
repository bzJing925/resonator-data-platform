import { WorkerProjectStore } from '../../src/features/analysis/project/WorkerProjectStore';
import { IndexedDbProjectStore } from '../../src/features/analysis/project/IndexedDbProjectStore';
import { createAnalysisDataset } from '../../src/features/analysis/model/dataset';
import { executeAnalysisCommand } from '../../src/features/analysis/model/commands';
import { INITIAL_WORKSPACE_STATE } from '../../src/features/analysis/workspace';
import { migrateLegacyExplore, restoreWorkspace, serializeWorkspace } from '../../src/features/analysis/project/projectCodec';
import type { ProjectHead } from '../../src/features/analysis/project/projectTypes';
import { platformRows } from '../../src/features/analysis/__fixtures__/platformFixtures';

const store = new URLSearchParams(location.search).has('worker') ? new WorkerProjectStore() : new IndexedDbProjectStore();
const browser = { store, WorkerProjectStore, IndexedDbProjectStore, restoreWorkspace, serializeWorkspace, executeAnalysisCommand,
  async create(count = 2001) {
    const dataset = createAnalysisDataset({ id: crypto.randomUUID(), name: '两块', kind: 'query', rows: platformRows(count) });
    const state = executeAnalysisCommand(INITIAL_WORKSPACE_STATE, { type: 'addDataset', dataset, activate: 'dataset' });
    const head: ProjectHead = { projectId: crypto.randomUUID(), currentVersionId: crypto.randomUUID(),
      generation: 1, name: '项目A', kind: 'named', schemaVersion: 1,
      updatedAt: new Date().toISOString(), lastOpenedAt: new Date().toISOString() };
    return store.save(serializeWorkspace(state, migrateLegacyExplore({}), head), null);
  },
  async counts() {
    return new Promise((resolve, reject) => {
      const open = indexedDB.open('aln_analysis_projects', 1);
      open.onerror = () => reject(open.error);
      open.onsuccess = () => {
        const db = open.result, tx = db.transaction(['heads', 'versions', 'chunks'], 'readonly');
        const values: Record<string, number> = {};
        for (const name of ['heads', 'versions', 'chunks']) {
          const count = tx.objectStore(name).count(); count.onsuccess = () => values[name] = count.result;
        }
        tx.oncomplete = () => { db.close(); resolve(values); };
      };
    });
  },
};
Object.assign(window, { projectHarness: browser });
