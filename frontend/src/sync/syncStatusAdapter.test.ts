import { afterEach, expect, it, vi } from 'vitest';
import { parseSyncStatus, legacyConnection, unknownSyncStatus } from './syncStatusAdapter';
import { assertWriteCapability, parseCapabilities, STATIC_CAPABILITIES } from '../api/capabilities';
afterEach(() => { vi.resetModules(); vi.unstubAllEnvs(); vi.doUnmock('../config'); });
it('known server SQLite capabilities are not inferred from Electron and malformed protocol fails', () => {
  const caps = parseCapabilities({ ...STATIC_CAPABILITIES, mode: 'server', database: 'sqlite' });
  expect(caps.mode).toBe('server'); expect(caps.database).toBe('sqlite');
  expect(() => parseCapabilities({ ...caps, features: {} })).toThrow('协议');
  expect(() => assertWriteCapability(null)).toThrow('能力未知');
  expect(() => assertWriteCapability(STATIC_CAPABILITIES)).toThrow('静态');
  expect(() => assertWriteCapability(caps, 'upload_precheck')).toThrow('不可用');
});
it('static capabilities and sync status do not issue backend HTTP even with desktop shell', async () => {
  vi.doMock('../config', () => ({ IS_STATIC: true }));
  const api = (await import('../api/client')).default, get = vi.spyOn(api, 'get');
  const { getCapabilities } = await import('../api/capabilities');
  const { getBatchSyncStatus } = await import('./syncStatusAdapter');
  expect((await getCapabilities()).database).toBe('duckdb');
  expect((await getBatchSyncStatus('A')).web.state).toBe('unknown');
  expect(get).not.toHaveBeenCalled(); get.mockRestore();
});
it('separate targets preserve confirmed/pending revisions and durable error code without toast inference', () => {
  const failed = { ...unknownSyncStatus('web'), state: 'failed', confirmed_revision: 2, pending_revision: 3, last_success_at: '2026-10-10T00:00:00Z', error_code: 'NETWORK_ERROR', retryable: true };
  expect(parseSyncStatus(failed, 'web')).toEqual(failed);
  expect(() => parseSyncStatus(failed, 'server')).toThrow('协议');
  expect(() => parseSyncStatus({ ...unknownSyncStatus('server'), state: 'confirmed' }, 'server')).toThrow('协议');
  expect(legacyConnection({ enabled: true, configured: true, reachable: true, server_url: 'http://server', username: 'u', unsynced_batches: ['A'] })).toMatchObject({ connection: '已连接', pending: ['A'], sync: { state: 'unknown', confirmed_revision: null } });
});

it('unknown capability rejects an actual API mutation before its transport runs', async () => {
  const api = (await import('../api/client')).default;
  const transport = vi.fn();
  await expect(api.post('/uploads', {}, { adapter: transport })).rejects.toThrow('能力未知');
  expect(transport).not.toHaveBeenCalled();
});
