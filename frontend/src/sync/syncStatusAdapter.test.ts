import { expect, it, vi } from 'vitest';
import { parseSyncStatus, legacyConnection, unknownSyncStatus } from './syncStatusAdapter';
import { assertWriteCapability, parseCapabilities } from '../api/capabilities';
import type { Capabilities } from '../types/platformContracts';
const BASE_CAPS: Capabilities = { schema_version: 1, mode: 'server', database: 'sqlite', protocols: [],
  features: { upload_precheck: false, processing_reports: false, projects: true, full_export_csv: true, full_export_xlsx: false, batch_compare: true, trash: false, replication_v3: false, remote_delete: false, credential_storage: false },
  limits: { full_export_max_rows: 200000, full_export_max_bytes: 100 * 1024 * 1024, comparison_max_rows: 200000 } };
it('known server SQLite capabilities are not inferred from Electron and malformed protocol fails', () => {
  const caps = parseCapabilities({ ...BASE_CAPS });
  expect(caps.mode).toBe('server'); expect(caps.database).toBe('sqlite');
  expect(() => parseCapabilities({ ...caps, features: {} })).toThrow('协议');
  expect(() => parseCapabilities({ ...caps, mode: 'static' })).toThrow('协议');
  expect(() => assertWriteCapability(null)).toThrow('能力未知');
  expect(() => assertWriteCapability(caps, 'upload_precheck')).toThrow('不可用');
});
it('server sync status keeps confirmed/pending revisions and durable error code without toast inference', () => {
  const failed = { ...unknownSyncStatus('server'), state: 'failed', confirmed_revision: 2, pending_revision: 3, last_success_at: '2026-10-10T00:00:00Z', error_code: 'NETWORK_ERROR', retryable: true };
  expect(parseSyncStatus(failed, 'server')).toEqual(failed);
  expect(() => parseSyncStatus({ ...unknownSyncStatus('server'), state: 'confirmed' }, 'server')).toThrow('协议');
  expect(legacyConnection({ enabled: true, configured: true, reachable: true, server_url: 'http://server', username: 'u', unsynced_batches: ['A'] })).toMatchObject({ connection: '已连接', pending: ['A'], sync: { state: 'unknown', confirmed_revision: null } });
});

it('unknown capability rejects an actual API mutation before its transport runs', async () => {
  const api = (await import('../api/client')).default;
  const transport = vi.fn();
  await expect(api.post('/uploads', {}, { adapter: transport })).rejects.toThrow('能力未知');
  expect(transport).not.toHaveBeenCalled();
});
