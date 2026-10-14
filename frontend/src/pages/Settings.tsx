import ReplicaPreferences from '../lifecycle/ReplicaPreferences';
import React, { useState } from 'react';
import { useCapabilities } from '../api/capabilities';
import ServerSyncPanel from '../components/ServerSyncPanel';
export default function Settings() {
  const { capabilities, error, refresh } = useCapabilities();
  const [showHints, setShowHints] = useState(() => { try { return localStorage.getItem('aln_settings_hints') !== 'false'; } catch { return true; } });
  return <main style={{ padding: 20, overflow: 'auto' }}><h1>设置</h1>
    <h2>连接配置与服务器同步</h2><p>{capabilities ? `${capabilities.mode} · ${capabilities.database}` : error || '能力未知，写操作禁用'}</p><button onClick={refresh}>重试能力</button>
    <ServerSyncPanel onChanged={() => {}} />
    <ReplicaPreferences />
    <h2>偏好</h2><label><input type="checkbox" checked={showHints} onChange={(event) => { setShowHints(event.target.checked); try { localStorage.setItem('aln_settings_hints', String(event.target.checked)); } catch { /* Session preference remains usable. */ } }} />显示操作提示</label>
  </main>;
}
