import { useEffect, useState } from 'react';
import { getServerSyncStatus, pullFromServer } from '../api/endpoints';

// 模块级标记：每次应用启动只自动同步一次（StrictMode 双挂载/路由切换不重复触发）
let attempted = false;

/** 启动时探测内网服务器：可达则自动下行同步一次并提示结果；不可达/未配置/失败均静默。
 * 上行回传涉及删除本地文件，仍由批次页面板手动触发。 */
export default function ServerAutoSync() {
  const [text, setText] = useState<string | null>(null);
  const [persistent, setPersistent] = useState(false);

  useEffect(() => {
    if (attempted) return;
    attempted = true;
    let alive = true;
    (async () => {
      try {
        const s = await getServerSyncStatus();
        if (!s.enabled || !s.configured || !s.reachable) return;
        const r = await pullFromServer();
        if (!alive) return;
        const fresh =
          r.batches_added === 0 && r.mappings_added === 0 && r.devices_added === 0;
        setText(
          fresh
            ? '服务器已连接，数据已是最新'
            : `已从服务器同步：新增批次 ${r.batches_added}、器件 ${r.devices_added}`
        );
        setPersistent(!fresh);
      } catch {
        // 自动同步失败静默处理；批次页"内网服务器同步"面板可查看状态并手动重试
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  // "已是最新"提示 8 秒后自动消失；有新数据的提示保留到手动关闭
  useEffect(() => {
    if (!text || persistent) return;
    const id = setTimeout(() => setText(null), 8000);
    return () => clearTimeout(id);
  }, [text, persistent]);

  if (!text) return null;
  return (
    <div
      role="status"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        margin: '8px 12px 0',
        padding: '6px 12px',
        background: 'var(--bg-panel)',
        border: '1px solid var(--border)',
        borderRadius: 4,
        fontSize: 12,
      }}
    >
      <span style={{ flex: 1 }}>{text}</span>
      <button
        aria-label="关闭"
        style={{ border: 'none', background: 'none', cursor: 'pointer', color: 'var(--fg-3)' }}
        onClick={() => setText(null)}
      >
        ×
      </button>
    </div>
  );
}
