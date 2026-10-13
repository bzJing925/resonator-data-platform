import { useEffect, useState } from 'react';
import { syncCoordinator } from '../sync/useSyncCoordinator';

// 开启软件、重新联网、窗口回到前台及每分钟尝试同步；请求不重叠。
export default function ServerAutoSync() {
  const [text, setText] = useState<string | null>(null);
  const [persistent, setPersistent] = useState(false);

  useEffect(() => {
    const unsubscribe = syncCoordinator.subscribe(() => {
      const r = syncCoordinator.snapshot.result;
      if (!r) return;
      const changed = r.batches_added + (r.mappings_added || 0) + (r.batches_updated || 0) +
        (r.batches_deleted || 0) + (r.tables_pushed || 0) + (r.mappings_pushed || 0) + (r.raw_pushed || 0);
      setText(r.errors?.length ? `部分同步未完成：${r.errors.join('；')}` : changed
        ? `同步完成：新增 ${r.batches_added}、更新 ${r.batches_updated || 0}、删除 ${r.batches_deleted || 0}、表格回传 ${r.tables_pushed || 0}、原始批次回传 ${r.raw_pushed || 0}、对照表上传 ${r.mappings_pushed || 0}、对照表收到 ${r.mappings_added || 0}`
        : '服务器已连接，数据已是最新');
      setPersistent(!!changed || !!r.errors?.length);
    });
    const stop = syncCoordinator.start();
    return () => { unsubscribe(); stop(); };
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
      {persistent && <button onClick={() => window.location.reload()}>刷新显示</button>}
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
