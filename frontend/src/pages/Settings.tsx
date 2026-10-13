import ReplicaPreferences from '../lifecycle/ReplicaPreferences';
import React, { useState } from 'react';
import { IS_STATIC } from '../config';
import { useCapabilities } from '../api/capabilities';
import ServerSyncPanel from '../components/ServerSyncPanel';
import { useSyncCoordinator } from '../sync/useSyncCoordinator';
import SyncStatusBadge from '../sync/SyncStatusBadge';
import { pushBatchToWeb } from '../api/endpoints';
import { useAnalysisWorkspace } from '../features/analysis/workspace';
export default function Settings() {
  const { capabilities, error, refresh } = useCapabilities(), coordinator = useSyncCoordinator();
  const { project } = useAnalysisWorkspace();
  const [token, setToken] = useState(''), [batch, setBatch] = useState(() => new URLSearchParams(window.location.search).get('batch') ?? ''), [message, setMessage] = useState('');
  const [showHints, setShowHints] = useState(() => { try { return localStorage.getItem('aln_settings_hints') !== 'false'; } catch { return true; } });
  const [confirmedPublish, setConfirmedPublish] = useState(false);
  return <main style={{ padding: 20, overflow: 'auto' }}><h1>设置</h1>
    <h2>连接配置与服务器同步</h2><p>{capabilities ? `${capabilities.mode} · ${capabilities.database}` : error || '能力未知，写操作禁用'}</p><button onClick={refresh}>重试能力</button>
    {IS_STATIC ? <p>静态快照版：只读发布数据；服务器同步与网页发布不可用。</p> : <ServerSyncPanel onChanged={() => {}} />}
    <h2>网页发布</h2>{!IS_STATIC && <>
      <label>批次号<input value={batch} onChange={(event) => { setBatch(event.target.value); setConfirmedPublish(false); }} /></label>
      <label>发布凭据（仅本次会话）<input type="password" value={token} autoComplete="off" onChange={(event) => setToken(event.target.value)} /></label>
      <label><input type="checkbox" checked={confirmedPublish} onChange={(event) => setConfirmedPublish(event.target.checked)} />确认人工发布所选批次</label>
      <button disabled={!capabilities?.features.replication_v3 || coordinator.busy || coordinator.conflict || !batch.trim() || !token || !confirmedPublish} onClick={async () => {
        const selected = batch.trim(), credential = token; setMessage('');
        try { await coordinator.run(`publish:${selected}`, () => pushBatchToWeb(selected, credential)); setMessage('已提交，等待实际发布快照确认；提交不是已同步。'); }
        catch (failure) { setMessage(`发布失败：${String(failure)}`); }
      }}>发布到网页</button>{batch.trim() && <SyncStatusBadge batchNo={batch.trim()} />}
    </>}
    {message && <p role="status">{message}</p>}
    <ReplicaPreferences />
    <h2>本地项目存储</h2><p>{project.sessionMode ? '仅会话，存储不可用；请导出JSON备份。' : `IndexedDB · ${project.status}`}。清除站点数据会删除项目，JSON备份可恢复。</p>
    <h2>偏好</h2><label><input type="checkbox" checked={showHints} onChange={(event) => { setShowHints(event.target.checked); try { localStorage.setItem('aln_settings_hints', String(event.target.checked)); } catch { /* Session preference remains usable. */ } }} />显示操作提示</label>
    <p>凭据不会写入项目、localStorage或Electron凭据接口；重新打开设置需重新输入。</p>
  </main>;
}
