import { useCallback, useEffect, useState } from 'react';
import {
  getServerSyncStatus, pullFromServer, pushBatchToServer, saveServerSyncConfig,
} from '../api/endpoints';
import type { ServerSyncStatus } from '../types';

/** 内网服务器同步面板：仅桌面模式（enabled）渲染。 */
export default function ServerSyncPanel({ onChanged }: { onChanged: () => void }) {
  const [status, setStatus] = useState<ServerSyncStatus | null>(null);
  const [url, setUrl] = useState('');
  const [username, setUsername] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    getServerSyncStatus()
      .then((s) => {
        setStatus(s);
        if (s.server_url) setUrl((v) => v || s.server_url || '');
        if (s.username) setUsername((v) => v || s.username || '');
      })
      .catch(() => setStatus(null));
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  if (!status || !status.enabled) return null;

  const run = async (action: () => Promise<string>) => {
    setBusy(true);
    setMessage(null);
    try {
      setMessage(await action());
      refresh();
      onChanged();
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setMessage(`失败：${detail || (e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        background: 'var(--bg-panel)',
        border: '1px solid var(--border)',
        borderRadius: 4,
        padding: '10px 14px',
        margin: 12,
      }}
    >
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <strong>内网服务器同步</strong>
        <span className="dim" style={{ fontSize: 11.5 }}>
          {status.configured
            ? status.reachable
              ? `已连接 ${status.server_url}`
              : `服务器不可达（需连接内网）${status.server_url ? `：${status.server_url}` : ''}`
            : '未配置服务器地址'}
        </span>
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
        <input
          style={{ flex: 2, minWidth: 220 }}
          placeholder="服务器地址，如 http://192.168.1.10:8080"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
        <input
          style={{ flex: 1, minWidth: 120 }}
          placeholder="用户名（记录上传人）"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
        <button
          disabled={busy || !url.trim()}
          onClick={() => run(async () => {
            await saveServerSyncConfig(url.trim(), username.trim() || 'anonymous');
            return '已保存配置';
          })}
        >
          保存配置
        </button>
        <button
          disabled={busy || !status.reachable}
          onClick={() => run(async () => {
            const r = await pullFromServer();
            return `同步完成：新增批次 ${r.batches_added}、器件 ${r.devices_added}`;
          })}
        >
          从服务器同步
        </button>
      </div>
      {status.unsynced_batches.length > 0 && (
        <div style={{ marginTop: 8, fontSize: 12 }}>
          <span className="dim">待回传（{status.unsynced_batches.length}）：</span>
          {status.unsynced_batches.map((b) => (
            <span key={b} style={{ marginRight: 8 }}>
              <code>{b}</code>
              <button
                style={{ marginLeft: 4 }}
                disabled={busy || !status.reachable}
                onClick={() => run(async () => {
                  const r = await pushBatchToServer(b);
                  return `${b} 已回传（${r.device_count ?? '-'} 条），本地原始文件已清理`;
                })}
              >
                回传
              </button>
            </span>
          ))}
        </div>
      )}
      {message && <div className="dim" style={{ marginTop: 8, fontSize: 11.5 }}>{message}</div>}
    </div>
  );
}
