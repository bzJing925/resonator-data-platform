import React, { useEffect, useRef, useState } from 'react';
import { deleteBatch, getDeletionImpact, hideReplica, deleteRemoteSource, getRemoteDeletionImpact, type DeletionImpact } from '../api/endpoints';
import { useCapabilities } from '../api/capabilities';
import { uploadError } from '../upload/types';
export default function DeletionImpactDialog({ batchNo, onClose, onChanged }: { batchNo: string; onClose: () => void; onChanged: () => void }) {
  const { capabilities } = useCapabilities();
  const [impact, setImpact] = useState<DeletionImpact | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false), [scope, setScope] = useState<'local' | 'remote'>('local'), [remoteImpact, setRemoteImpact] = useState<DeletionImpact | null>(null);
  const lock = useRef(false), alive = useRef(true);
  useEffect(() => { let stopped = false; alive.current = true; getDeletionImpact(batchNo).then((value) => { if (!stopped) setImpact(value); }).catch((failure) => { if (!stopped) setError(uploadError(failure)); }); return () => { stopped = true; alive.current = false; }; }, [batchNo]);
  const replica = impact && ['server', 'web', 'server_owned'].includes(impact.scope);
  const remote = replica && scope === 'remote';
  const displayed = remote ? remoteImpact : impact;
  const allowed = !!capabilities?.features.trash && !!displayed && (!remote || (capabilities.features.remote_delete && capabilities.protocols.includes('sync-v3') && impact?.source_ref.source_revision != null));
  return <div role="dialog" aria-label="删除影响" className="chart-card"><h3>删除影响 · {batchNo}</h3>
    {impact && <><p>器件 {displayed?.device_count ?? '未知'}；原始输入 {displayed?.raw_input_available ? '可用' : '不可用'}；作用域 {remote ? '服务器来源' : impact.scope}；保留30天，预计清理 {impact.purge_after}</p>
      {displayed?.active_task_id != null && <p role="alert">任务 {displayed.active_task_id} 尚未确认结束，禁止删除。</p>}
      {replica && <><label><input type="radio" checked={scope === 'local'} onChange={() => setScope('local')} />仅移除本地副本（隐藏，可重新显示）</label><label><input type="radio" checked={scope === 'remote'} disabled={!capabilities?.features.remote_delete || !capabilities.protocols.includes('sync-v3')} onChange={async () => { setScope('remote'); setRemoteImpact(null); setError(''); try { const value = await getRemoteDeletionImpact(impact.source_ref); if (value.source_ref.origin_id !== impact.source_ref.origin_id || value.source_ref.source_batch_id !== impact.source_ref.source_batch_id || value.source_ref.source_revision !== impact.source_ref.source_revision) throw new Error('来源版本已变化，请重新同步并核对，未删除'); if (alive.current) setRemoteImpact(value); } catch (failure) { if (alive.current) setError(uploadError(failure)); } }} />删除服务器批次（影响所有客户端）</label></>}
      <p>{remote ? '明确操作UUID来源的服务器数据；原版本变化将拒绝，不自动覆盖。' : replica ? '只修改本地显示偏好，不删除服务器数据。' : '批次移入回收站，保留期内可恢复。'}</p>
    </>}
    {error && <p role="alert">{error}</p>}
    <button disabled={busy} onClick={onClose}>取消</button>
    <button disabled={busy || !impact || !allowed || (displayed?.active_task_id != null && (!replica || remote))} onClick={async () => {
      if (lock.current || !impact) return; lock.current = true; setBusy(true); setError('');
      try { if (remote) await deleteRemoteSource(impact.source_ref); else if (replica) await hideReplica(impact.source_ref, true); else await deleteBatch(batchNo); if (alive.current) onChanged(); }
      catch (failure) { if (alive.current) setError(uploadError(failure)); }
      finally { lock.current = false; if (alive.current) setBusy(false); }
    }}>{remote ? '确认删除服务器批次' : replica ? '确认仅移除本地副本' : '确认移入回收站'}</button>
  </div>;
}
