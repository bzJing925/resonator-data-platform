import { afterEach, expect, it, vi } from 'vitest';
import { SyncCoordinator } from './useSyncCoordinator';
import type { Capabilities } from '../types/platformContracts';
const status = { enabled: true, configured: true, reachable: true, server_url: 'http://server', username: 'u', unsynced_batches: [] };
const caps: Capabilities = { schema_version: 1, mode: 'desktop', database: 'sqlite', protocols: [], limits: {},
  features: { upload_precheck: true, processing_reports: true, projects: true, full_export_csv: true, full_export_xlsx: true, batch_compare: true, trash: true, replication_v3: true, remote_delete: true, credential_storage: false } };
function deps() { return { status: vi.fn().mockResolvedValue(status), capabilities: vi.fn().mockResolvedValue(caps), pull: vi.fn().mockResolvedValue({ batches_added: 0, mappings_added: 0, devices_added: 0 }) }; }
afterEach(() => vi.useRealTimers());
it('automatic and manual sync coalesce one in-flight write', async () => {
  const dependency = deps(); let finish: (value: any) => void = () => {};
  dependency.pull.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const coordinator = new SyncCoordinator(dependency);
  const a = coordinator.synchronize(), b = coordinator.synchronize(); expect(a).toBe(b);
  await vi.waitFor(() => expect(dependency.pull).toHaveBeenCalledTimes(1));
  finish({ batches_added: 0, mappings_added: 0, devices_added: 0 }); await a;
  expect(coordinator.snapshot.busy).toBe(false);
});
it('409/partial conflicts halt future auto writes; read refresh never changes revision or retries pull', async () => {
  const dependency = deps(); dependency.pull.mockResolvedValue({ batches_added: 1, mappings_added: 0, devices_added: 0, errors: ['A: TABLE_CONFLICT 409'] });
  const coordinator = new SyncCoordinator(dependency);
  await coordinator.synchronize(); expect(coordinator.snapshot.conflict).toBe(true);
  await coordinator.synchronize(); await coordinator.refresh();
  expect(dependency.pull).toHaveBeenCalledTimes(1); expect(dependency.status).toHaveBeenCalledTimes(2);
  await expect(coordinator.run('push', async () => {})).rejects.toThrow('冲突');
});
it('only one timer for multiple owners; unload prevents a pending status from triggering later write', async () => {
  vi.useFakeTimers(); const dependency = deps(); let resolveStatus: (value: any) => void = () => {};
  dependency.status.mockImplementation(() => new Promise((resolve) => { resolveStatus = resolve; }));
  const coordinator = new SyncCoordinator(dependency), stopA = coordinator.start(), stopB = coordinator.start();
  await Promise.resolve(); expect(vi.getTimerCount()).toBe(1);
  stopA(); expect(vi.getTimerCount()).toBe(1); stopB(); expect(vi.getTimerCount()).toBe(0);
  resolveStatus(status); await Promise.resolve(); await Promise.resolve(); expect(dependency.pull).not.toHaveBeenCalled();
});
it('ordinary network failure allows later online retry', async () => {
  const dependency = deps();
  const coordinator = new SyncCoordinator(dependency); dependency.pull.mockRejectedValueOnce(new Error('offline'));
  await expect(coordinator.synchronize()).rejects.toThrow('offline'); expect(coordinator.snapshot.conflict).toBe(false);
  await coordinator.synchronize(); expect(dependency.pull).toHaveBeenCalledTimes(2);
});

it('persisted backend conflict blocks auto sync after a fresh coordinator starts', async () => {
  const dependency = deps(); dependency.status.mockResolvedValue({ ...status, unsynced_batches: ['A'] });
  const batchStatus = vi.fn().mockResolvedValue({ server: { state: 'conflict', error_code: 'BASE_REVISION_CONFLICT' } });
  const coordinator = new SyncCoordinator({ ...dependency, batchStatus });
  await coordinator.synchronize(); expect(coordinator.snapshot.conflict).toBe(true);
  expect(dependency.pull).not.toHaveBeenCalled(); expect(coordinator.snapshot.errors).toEqual(['A: BASE_REVISION_CONFLICT']);
});
