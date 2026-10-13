import { useEffect, useState } from 'react';
import { IS_STATIC } from '../config';
import { getServerSyncStatus, pullFromServer } from '../api/endpoints';
import { getBatchSyncStatus } from './syncStatusAdapter';
import { getCapabilities } from '../api/capabilities';
import type { ServerSyncStatus } from '../types';
export type SyncResult = Awaited<ReturnType<typeof pullFromServer>>;
export function hasConflict(value: unknown) { return /409|CONFLICT|冲突|内容不同/i.test(String(value)); }
export class SyncCoordinator {
  private pending: { key: string; promise: Promise<any> } | null = null;
  private listeners = new Set<() => void>();
  private epoch = 0;
  private owners = 0;
  private timer: ReturnType<typeof setInterval> | null = null;
  snapshot = { status: null as ServerSyncStatus | null, busy: false, conflict: false, errors: [] as string[], result: null as SyncResult | null };
  constructor(private dependencies: { status: typeof getServerSyncStatus; pull: typeof pullFromServer; capabilities: typeof getCapabilities; batchStatus?: typeof getBatchSyncStatus } = { status: getServerSyncStatus, pull: pullFromServer, capabilities: getCapabilities, batchStatus: getBatchSyncStatus }, private staticMode = IS_STATIC) {}
  subscribe(listener: () => void) { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; }
  private emit() { this.snapshot = { ...this.snapshot }; for (const listener of this.listeners) listener(); }
  async refresh() {
    if (this.staticMode) return null;
    const epoch = this.epoch;
    try { const status = await this.dependencies.status(); if (epoch === this.epoch) { this.snapshot.status = status; this.emit(); } return status; }
    catch (failure) { if (epoch === this.epoch) { this.snapshot.status = null; this.emit(); } throw failure; }
  }
  run<T>(key: string, action: () => Promise<T>): Promise<T> {
    if (this.staticMode) return Promise.reject(new Error('静态版不支持同步写操作'));
    if (this.snapshot.conflict) return Promise.reject(new Error('同步冲突；只读刷新可核对状态，不能自动覆盖或重基版本'));
    if (this.pending) return this.pending.key === key ? this.pending.promise : Promise.reject(new Error('同步操作进行中'));
    const epoch = this.epoch; this.snapshot.busy = true; this.emit();
    const promise = Promise.resolve().then(() => { if (epoch !== this.epoch) throw new Error('同步作用域已关闭'); return action(); }).catch((failure) => {
      if (epoch === this.epoch && hasConflict((failure as any)?.response?.status ?? failure)) { this.snapshot.conflict = true; this.snapshot.errors = [String((failure as any)?.response?.data?.detail ?? failure)]; }
      throw failure;
    }).finally(() => { this.pending = null; this.snapshot.busy = false; if (epoch === this.epoch) this.emit(); });
    this.pending = { key, promise }; return promise;
  }
  synchronize(): Promise<SyncResult | null> {
    if (this.staticMode || this.snapshot.conflict) return Promise.resolve(null);
    const epoch = this.epoch;
    return this.run('synchronize', async () => {
      const status = await this.refresh();
      if (epoch !== this.epoch || !status?.enabled || !status.configured || !status.reachable) return null;
      const caps = await this.dependencies.capabilities();
      if (epoch !== this.epoch || caps.mode !== 'desktop' || !caps.features.replication_v3) return null;
      if (this.dependencies.batchStatus) {
        for (const batch of status.unsynced_batches) {
          const durable = await this.dependencies.batchStatus(batch);
          if (epoch !== this.epoch) return null;
          if (durable.server.state === 'conflict') {
            this.snapshot.conflict = true; this.snapshot.errors = [`${batch}: ${durable.server.error_code ?? 'SYNC_CONFLICT'}`]; this.emit(); return null;
          }
        }
      }
      const result = await this.dependencies.pull();
      if (epoch === this.epoch) {
        this.snapshot.result = result; this.snapshot.errors = result.errors ?? [];
        this.snapshot.conflict = this.snapshot.errors.some(hasConflict); this.emit();
        if (result.batches_added + (result.batches_updated ?? 0) + (result.batches_deleted ?? 0) + (result.mappings_added ?? 0) + (result.tables_pushed ?? 0) + (result.raw_pushed ?? 0) + (result.mappings_pushed ?? 0)) window.dispatchEvent(new Event('aln-data-synced'));
      }
      return result;
    });
  }
  start() {
    if (this.staticMode) return () => {};
    this.owners++;
    if (this.owners === 1) {
      const tick = () => { void this.synchronize().catch(() => {}); };
      this.tick = tick; tick(); this.timer = setInterval(tick, 60000);
      for (const event of ['online', 'focus', 'aln-local-data-changed']) window.addEventListener(event, tick);
    }
    return () => {
      if (--this.owners > 0) return;
      this.epoch++; if (this.timer) clearInterval(this.timer); this.timer = null;
      for (const event of ['online', 'focus', 'aln-local-data-changed']) if (this.tick) window.removeEventListener(event, this.tick);
    };
  }
  private tick: (() => void) | null = null;
}
export const syncCoordinator = new SyncCoordinator();
export function useSyncCoordinator() {
  const [snapshot, setSnapshot] = useState(syncCoordinator.snapshot);
  useEffect(() => syncCoordinator.subscribe(() => setSnapshot(syncCoordinator.snapshot)), []);
  return { ...snapshot, refresh: () => syncCoordinator.refresh(), synchronize: () => syncCoordinator.synchronize(), run: <T,>(key: string, action: () => Promise<T>) => syncCoordinator.run(key, action) };
}
