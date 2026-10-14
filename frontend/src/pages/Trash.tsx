import React, { useEffect, useRef, useState } from 'react';
import { useCapabilities } from '../api/capabilities';
import { getTrash, restoreBatch, type DeletionImpact } from '../api/endpoints';
import { uploadError } from '../upload/types';
export default function Trash() {
  const { capabilities } = useCapabilities();
  const [items, setItems] = useState<DeletionImpact[]>([]), [error, setError] = useState(''), [page, setPage] = useState(1), [total, setTotal] = useState<number | null>(null), [busy, setBusy] = useState(false);
  const alive = useRef(true), lock = useRef(false);
  const refresh = async () => { try { const result = await getTrash(page); if (alive.current) { setItems(result.items); setTotal(result.total); setError(''); } } catch (failure) { if (alive.current) setError(uploadError(failure)); } };
  useEffect(() => { alive.current = true; void refresh(); return () => { alive.current = false; }; }, [page]);
  return <main style={{ padding: 20 }}><h1>回收站</h1><p>批次软删保留30天；恢复失败保留当前列表，已清理不可恢复，重名不覆盖。</p>{error && <p role="alert">{error}</p>}
    {items.map((item) => <article key={`${item.source_ref.origin_id}:${item.source_ref.source_batch_id}`}><h3>{item.batch_no}</h3><p>{item.device_count} 器件 · 清理时间 {item.purge_after}</p><button disabled={busy || !capabilities?.features.trash} onClick={async () => {
      if (lock.current) return; lock.current = true; setBusy(true);
      try { await restoreBatch(item.batch_no); await refresh(); } catch (failure) { if (alive.current) setError(uploadError(failure)); }
      finally { lock.current = false; if (alive.current) setBusy(false); }
    }}>恢复 {item.batch_no}</button></article>)}
    <button disabled={page === 1 || busy} onClick={() => setPage((p) => p - 1)}>上一页</button><span>{page} / {total == null ? '未知' : Math.max(1, Math.ceil(total / 20))}</span><button disabled={busy || total == null || page * 20 >= total} onClick={() => setPage((p) => p + 1)}>下一页</button>
  </main>;
}
