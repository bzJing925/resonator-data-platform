import { serializeWorkspace } from './projectCodec';
import { createSelectionState } from '../model/selection';
import type { WorkspaceState } from '../workspace';
import type { ProjectHead } from './projectTypes';
import { IndexedDbProjectStore } from './IndexedDbProjectStore';
import { ProjectValueReader, sendProjectValue } from './projectWire';
import { ProjectError } from './projectTypes';
const store = new IndexedDbProjectStore();
const readers = new Map<string, ProjectValueReader>();
const scope = self as unknown as { postMessage(message: unknown, transfer?: Transferable[]): void; onmessage: ((event: MessageEvent) => void) | null };
const methods = ['list', 'load', 'save', 'remove', 'saveTemplate', 'listTemplates', 'markOpened', 'collectOrphans'] as const;
scope.onmessage = (event) => {
  const { id, type, buffer } = event.data;
  if (type === 'begin') readers.set(id, new ProjectValueReader());
  else if (type === 'chunk') readers.get(id)?.append(buffer);
  else if (type === 'end') {
    const reader = readers.get(id); readers.delete(id);
    void (async () => {
      try {
        if (!reader) throw new ProjectError('CORRUPT', '项目传输不完整');
        const { method, args } = reader.value as { method: typeof methods[number]; args: unknown[] };
        let result: unknown;
        if (String(method) === 'saveWorkspace') {
          const [state, head, generation] = args as [WorkspaceState, ProjectHead, number | null];
          const input = serializeWorkspace({ ...state, selection: createSelectionState() }, state.spec, head, state.notes);
          input.version.extras = structuredClone(state.projectExtras ?? {});
          result = await store.save(input, generation);
        } else {
          if (!methods.includes(method)) throw new ProjectError('CORRUPT', '未知存储操作');
          const call = store[method] as (...values: unknown[]) => Promise<unknown>;
          result = await call.apply(store, args);
        }
        scope.postMessage({ id, type: 'begin' });
        await sendProjectValue(result, (bytes) => scope.postMessage({ id, type: 'chunk', buffer: bytes }, [bytes]));
        scope.postMessage({ id, type: 'end' });
      } catch (error) {
        scope.postMessage({ id, type: 'error', code: error instanceof ProjectError ? error.code : 'UNAVAILABLE', message: error instanceof Error ? error.message : '项目存储失败' });
      }
    })();
  }
};
