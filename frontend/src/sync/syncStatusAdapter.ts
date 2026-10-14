import api from '../api/client';
import type { SyncStatus } from '../types/platformContracts';
import type { ServerSyncStatus } from '../types';
export function unknownSyncStatus(target: SyncStatus['target'], code: string | null = null): SyncStatus {
  return { target, state: 'unknown', confirmed_revision: null, pending_revision: null, last_success_at: null, error_code: code, retryable: false };
}
export function parseSyncStatus(value: unknown, target: SyncStatus['target']): SyncStatus {
  if (!value || typeof value !== 'object') throw new Error('同步状态缺失');
  const v = value as SyncStatus;
  const revision = (x: unknown) => x === null || typeof x === 'number' && Number.isSafeInteger(x) && x >= 0;
  if (v.target !== target || !['local_only', 'pending', 'syncing', 'confirmed', 'conflict', 'failed', 'unknown'].includes(v.state)
    || !revision(v.confirmed_revision) || !revision(v.pending_revision) || typeof v.retryable !== 'boolean'
    || !(v.error_code === null || typeof v.error_code === 'string') || !(v.last_success_at === null || typeof v.last_success_at === 'string' && Number.isFinite(Date.parse(v.last_success_at)))
    || v.state === 'confirmed' && (v.confirmed_revision === null || v.last_success_at === null)) throw new Error('同步状态协议无效');
  return { target: v.target, state: v.state, confirmed_revision: v.confirmed_revision, pending_revision: v.pending_revision, last_success_at: v.last_success_at, error_code: v.error_code, retryable: v.retryable };
}
export async function getBatchSyncStatus(batchNo: string) {
  const { data } = await api.get(`/batches/${encodeURIComponent(batchNo)}/sync-status`);
  return { server: parseSyncStatus(data.server, 'server') };
}
export function legacyConnection(status: ServerSyncStatus | null) {
  return { connection: !status ? '未知' : status.reachable ? '已连接' : '不可达', pending: status?.unsynced_batches ?? null, sync: unknownSyncStatus('server') };
}
