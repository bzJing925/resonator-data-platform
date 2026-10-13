import { useCallback, useEffect, useState } from 'react';
import {
  pushBatchToServer, saveServerSyncConfig,
} from '../api/endpoints';
import { useSyncCoordinator } from '../sync/useSyncCoordinator';
import { useCapabilities } from '../api/capabilities';

/** 内网服务器同步面板：仅桌面模式（enabled）渲染。 */
export default function ServerSyncPanel({ onChanged }: { onChanged: () => void }) {
  const coordinator = useSyncCoordinator();
  const status = coordinator.status;
  const { capabilities } = useCapabilities();
  const cannotWrite = !capabilities?.features.replication_v3 || coordinator.conflict || coordinator.busy;
  const [url, setUrl] = useState('');
  const [username, setUsername] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    coordinator.refresh()
      .then((s) => {
        if (!s) return;
        if (s.server_url) setUrl((v) => v || s.server_url || '');
        if (s.username) setUsername((v) => v || s.username || '');
      })
      .catch(() => {});
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  if (!status || !status.enabled) return null;

  const run = async (action: () => Promise<string>) => {
    setBusy(true);
    setMessage(null);
    try {
      setMessage(await coordinator.run('manual', action));
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
          placeholder="服务器地址，如 http://192.168.1.10:8000"
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
          disabled={cannotWrite || busy || !url.trim()}
          onClick={() => run(async () => {
            await saveServerSyncConfig(url.trim(), username.trim() || 'anonymous');
            return '已保存配置';
          })}
        >
          保存配置
        </button>
        <button
          disabled={cannotWrite || busy || !status.reachable}
          onClick={async () => {
            setBusy(true); setMessage(null);
            try {
              const r = await coordinator.synchronize();
              if (!r) { setMessage('当前连接不支持同步'); return; }
              setMessage(`${r.errors?.length ? '部分同步未完成' : '同步请求结束'}：新增 ${r.batches_added}、更新 ${r.batches_updated || 0}、删除 ${r.batches_deleted || 0}、表格回传 ${r.tables_pushed || 0}、原始批次回传 ${r.raw_pushed || 0}、对照表上传 ${r.mappings_pushed || 0}、对照表收到 ${r.mappings_added || 0}。${r.errors?.join("；") || ""}`);
              onChanged();
            } catch (failure) { setMessage(String(failure)); } finally { setBusy(false); }
          }}
        >
          立即双向同步
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
                disabled={cannotWrite || busy || !status.reachable}
                onClick={() => run(async () => {
                  const r = await pushBatchToServer(b);
                  return `${b} 回传已提交；以持久同步状态为准（${r.device_count ?? '-'} 条），本地原始文件保留`;
                })}
              >
                回传
              </button>
            </span>
          ))}
        </div>
      )}
      {coordinator.conflict && <p role="alert">同步冲突，已停止自动重试；请核对失败资源及源版本。</p>}
      {coordinator.errors.map((error) => <p key={error}>{error}</p>)}
      <button onClick={refresh}>只读刷新连接</button>
      {message && <div className="dim" style={{ marginTop: 8, fontSize: 11.5 }}>{message}</div>}
    </div>
  );
}
