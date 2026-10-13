import { useCallback, useEffect, useState } from 'react';
import api from './client';
import { IS_STATIC } from '../config';
import type { Capabilities, CapabilityFeatures } from '../types/platformContracts';
export const STATIC_CAPABILITIES: Capabilities = { schema_version: 1, mode: 'static', database: 'duckdb', protocols: [],
  features: { upload_precheck: false, processing_reports: false, projects: true, full_export_csv: true, full_export_xlsx: false, batch_compare: true, trash: false, replication_v3: false, remote_delete: false, credential_storage: false },
  limits: { full_export_max_rows: 200000, full_export_max_bytes: 100 * 1024 * 1024, comparison_max_rows: 200000 } };
let current: Capabilities | null = null;
let inflight: Promise<Capabilities> | null = null;
const listeners = new Set<() => void>();
export function parseCapabilities(value: unknown): Capabilities {
  if (!value || typeof value !== 'object') throw new Error('能力协议无效');
  const v = value as Capabilities;
  if (v.schema_version !== 1 || !['server', 'desktop'].includes(v.mode) || !['sqlite', 'postgresql'].includes(v.database)
    || !Array.isArray(v.protocols) || v.protocols.some((p) => typeof p !== 'string') || !v.features || !v.limits
    || Object.keys(STATIC_CAPABILITIES.features).some((key) => typeof v.features[key] !== 'boolean')) throw new Error('能力协议无效');
  return structuredClone(v);
}
export function readCapabilities() { return IS_STATIC ? STATIC_CAPABILITIES : current; }
export async function getCapabilities(): Promise<Capabilities> {
  if (IS_STATIC) return STATIC_CAPABILITIES;
  if (inflight) return inflight;
  inflight = api.get('/capabilities').then((response) => {
    current = parseCapabilities(response.data); return current;
  }).catch((error) => { current = null; throw error; }).finally(() => { inflight = null; for (const listener of listeners) listener(); });
  return inflight;
}
export function useCapabilities() {
  const [capabilities, setCapabilities] = useState(readCapabilities), [error, setError] = useState('');
  const refresh = useCallback(async () => { setError(''); try { await getCapabilities(); } catch { setError('能力未知，重试；写操作已禁用'); } setCapabilities(readCapabilities()); }, []);
  useEffect(() => { const listener = () => setCapabilities(readCapabilities()); listeners.add(listener); void refresh(); return () => { listeners.delete(listener); }; }, [refresh]);
  return { capabilities, error, refresh };
}
export function assertWriteCapability(caps: Capabilities | null, feature?: keyof CapabilityFeatures) {
  if (!caps) throw new Error('能力未知，请在设置中重试；写操作未发送');
  if (caps.mode === 'static') throw new Error('静态快照版不支持服务器写操作');
  if (feature && !caps.features[feature]) throw new Error(`当前环境能力不可用：${feature}`);
}
export function writeFeature(url: string): keyof CapabilityFeatures | undefined {
  if (/^\/sync\/v3\/sources\//.test(url)) return 'remote_delete';
  if (/^\/replica-preferences/.test(url)) return 'replication_v3';
  if (/^\/uploads/.test(url)) return 'upload_precheck';
  if (/\/(reextract|redeembed|recompute|retry)$/.test(url)) return 'upload_precheck';
  if (/^\/tasks/.test(url)) return 'processing_reports';
  if (/^\/export\/jobs/.test(url)) return 'full_export_csv';
  if (/^\/trash|\/restore$/.test(url)) return 'trash';
  if (/^\/(desktop-sync|web-sync|sync)/.test(url)) return 'replication_v3';
  return undefined;
}
