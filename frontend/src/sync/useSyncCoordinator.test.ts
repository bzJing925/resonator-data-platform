import { afterEach, expect, it, vi } from 'vitest';
import { SyncCoordinator } from './useSyncCoordinator';
import { STATIC_CAPABILITIES } from '../api/capabilities';
const status = { enabled: true, configured: true, reachable: true, server_url: 'http://server', username: 'u', unsynced_batches: [] };
const caps = { ...STATIC_CAPABILITIES, mode: 'desktop' as const, database: 'sqlite' as const, features: { ...STATIC_CAPABILITIES.features, replication_v3: true } };
function deps() { return { status: vi.fn().mockResolvedValue(status), capabilities: vi.fn().mockResolvedValue(caps), pull: vi.fn().mockResolvedValue({ batches_added: 0, mappings_added: 0, devices_added: 0 }) }; }
afterEach(() => vi.useRealTimers());
it('automatic and manual sync coalesce one in-flight write', async () => {
  const dependency = deps(); let finish: (value: any) => void = () => {};
  dependency.pull.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const coordinator = new SyncCoordinator(dependency, false);
  const a = coordinator.synchronize(), b = coordinator.synchronize(); expect(a).toBe(b);
  await vi.waitFor(() => expect(dependency.pull).toHaveBeenCalledTimes(1));
  finish({ batches_added: 0, mappings_added: 0, devices_added: 0 }); await a;
  expect(coordinator.snapshot.busy).toBe(false);
});
it('409/partial conflicts halt future auto writes; read refresh never changes revision or retries pull', async () => {
  const dependency = deps(); dependency.pull.mockResolvedValue({ batches_added: 1, mappings_added: 0, devices_added: 0, errors: ['A: TABLE_CONFLICT 409'] });
  const coordinator = new SyncCoordinator(dependency, false);
  await coordinator.synchronize(); expect(coordinator.snapshot.conflict).toBe(true);
  await coordinator.synchronize(); await coordinator.refresh();
  expect(dependency.pull).toHaveBeenCalledTimes(1); expect(dependency.status).toHaveBeenCalledTimes(2);
  await expect(coordinator.run('push', async () => {})).rejects.toThrow('冲突');
});
it('only one timer for multiple owners; unload prevents a pending status from triggering later write', async () => {
  vi.useFakeTimers(); const dependency = deps(); let resolveStatus: (value: any) => void = () => {};
  dependency.status.mockImplementation(() => new Promise((resolve) => { resolveStatus = resolve; }));
  const coordinator = new SyncCoordinator(dependency, false), stopA = coordinator.start(), stopB = coordinator.start();
  await Promise.resolve(); expect(vi.getTimerCount()).toBe(1);
  stopA(); expect(vi.getTimerCount()).toBe(1); stopB(); expect(vi.getTimerCount()).toBe(0);
  resolveStatus(status); await Promise.resolve(); await Promise.resolve(); expect(dependency.pull).not.toHaveBeenCalled();
});
it('static is HTTP-free and ordinary network failure allows later online retry', async () => {
  const dependency = deps(), staticCoordinator = new SyncCoordinator(dependency, true);
  await staticCoordinator.synchronize(); await staticCoordinator.refresh(); expect(dependency.status).not.toHaveBeenCalled();
  const coordinator = new SyncCoordinator(dependency, false); dependency.pull.mockRejectedValueOnce(new Error('offline'));
  await expect(coordinator.synchronize()).rejects.toThrow('offline'); expect(coordinator.snapshot.conflict).toBe(false);
  await coordinator.synchronize(); expect(dependency.pull).toHaveBeenCalledTimes(2);
});

it('persisted backend conflict blocks auto sync after a fresh coordinator starts', async () => {
  const dependency = deps(); dependency.status.mockResolvedValue({ ...status, unsynced_batches: ['A'] });
  const batchStatus = vi.fn().mockResolvedValue({ server: { state: 'conflict', error_code: 'BASE_REVISION_CONFLICT' } });
  const coordinator = new SyncCoordinator({ ...dependency, batchStatus }, false);
  await coordinator.synchronize(); expect(coordinator.snapshot.conflict).toBe(true);
  expect(dependency.pull).not.toHaveBeenCalled(); expect(coordinator.snapshot.errors).toEqual(['A: BASE_REVISION_CONFLICT']);
});
