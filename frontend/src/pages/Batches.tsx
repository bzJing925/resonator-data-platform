import DeletionImpactDialog from '../lifecycle/DeletionImpactDialog';
import React, { memo, useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import I from '../components/Icons';
import { listBatches, renameBatch } from '../api/endpoints';
import RenameBatchModal from '../components/RenameBatchModal';
import { usePageState } from '../contexts/PageStateContext';
import type { Batch } from '../types';

interface BatchRowProps {
  batch: Batch;
  onDelete: (batch: Batch) => void;
  onRename: (batchNo: string) => void;
}

const BatchRow = memo(function BatchRow({ batch, onDelete, onRename }: BatchRowProps) {
  const b = batch;
  return (
    <tr>
      <td>
        <Link
          to={`/batches/${encodeURIComponent(b.batch_no)}`}
          style={{ color: 'var(--fg-1)', fontWeight: 600, textDecoration: 'none' }}
        >
          {b.batch_no}
        </Link>
        {b.synced_at && (
          <span className="dim" style={{ fontSize: 11, marginLeft: 6 }} title="原始文件已回传服务器">
            ☁️ 已回传
          </span>
        )}
        {b.source === 'server' && (
          <span className="dim" style={{ fontSize: 11, marginLeft: 6 }} title="来自服务器快照，原始文件在服务器上">
            ☁️ 服务器
          </span>
        )}
      </td>
      <td>
        <span className="chip">{b.mapping_name || '—'}</span>
      </td>
      <td className="num">{b.device_count?.toLocaleString() || 0}</td>
      <td className="num">
        {b.f_start_ghz != null && b.f_end_ghz != null
          ? `${b.f_start_ghz} – ${b.f_end_ghz} GHz`
          : '全频段'}
      </td>
      <td>{b.deembedded ? '是' : '—'}</td>
      <td>{b.process_type === 'BOTH' ? '双端口' : b.process_type === 'S1P' ? 'S1P' : b.process_type === 'S2P' ? 'S2P' : (b.process_type || '—')}</td>
      <td className="mono dim" style={{ fontSize: 11 }}>
        {b.uploaded_at ? new Date(b.uploaded_at).toLocaleString() : '—'}
      </td>
      <td>
        <div className="row-flex" style={{ gap: 4 }}>
          <Link
            to={`/batches/${encodeURIComponent(b.batch_no)}`}
            className="btn ghost sm"
            title="详情"
            style={{ textDecoration: 'none' }}
          >
            <I.table size={12} />
          </Link>
          <button className="btn ghost sm" title="重命名" onClick={() => onRename(b.batch_no)}>
            <I.edit size={12} />
          </button>
          <button className="btn ghost sm danger" title="删除" onClick={() => onDelete(b)}>
            <I.trash size={12} />
          </button>
        </div>
      </td>
    </tr>
  );
});

const BATCHES_INITIAL_STATE = { page: 1, search: '' };

export default function Batches() {
  const [deleting, setDeleting] = useState<string | null>(null);
  const [state, setState] = usePageState('batches', BATCHES_INITIAL_STATE);
  const { page, search } = state;
  const setPage = useCallback((v: number | ((prev: number) => number)) => setState((s) => ({ ...s, page: typeof v === 'function' ? v(s.page) : v })), [setState]);
  const setSearch = useCallback((v: string | ((prev: string) => string)) => setState((s) => ({ ...s, search: typeof v === 'function' ? v(s.search) : v })), [setState]);

  const [size] = useState<number>(20);
  const [data, setData] = useState<{ items: Batch[]; total: number; page: number; size: number }>({
    items: [],
    total: 0,
    page: 1,
    size: 20,
  });
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [renameBatchNo, setRenameBatchNo] = useState<string | null>(null);

  // load 接受外部 cancelled flag —— useEffect 重跑时能让旧请求丢弃结果，
  // 避免分页/size 快速切换时慢请求覆盖快请求结果。
  const load = (cancelled = { current: false }) => {
    setLoading(true);
    listBatches({ page, size, sort: '-uploaded_at' })
      .then((d) => { if (!cancelled.current) setData(d as { items: Batch[]; total: number; page: number; size: number }); })
      .catch((e: Error) => { if (!cancelled.current) setError(e.message); })
      .finally(() => { if (!cancelled.current) setLoading(false); });
  };

  useEffect(() => {
    const cancelled = { current: false };
    load(cancelled);
    return () => { cancelled.current = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, size]);

  const onDelete = (batch: Batch) => setDeleting(batch.batch_no);

  const handleRename = async (oldBatchNo: string, newBatchNo: string) => {
    try {
      await renameBatch(oldBatchNo, newBatchNo);
      setRenameBatchNo(null);
      load();
    } catch (e: any) {
      alert(e.response?.data?.detail || e.message);
    }
  };

  const filtered = data.items.filter(
    (b) =>
      !search ||
      b.batch_no.toLowerCase().includes(search.toLowerCase()) ||
      (b.mapping_name || '').toLowerCase().includes(search.toLowerCase())
  );

  return (
    <>
      {deleting && <DeletionImpactDialog batchNo={deleting} onClose={() => setDeleting(null)} onChanged={() => { setDeleting(null); load(); }} />}
      <div className="toolbar">
        <span className="crumb">
          <b>批次管理</b>
        </span>
        <div className="divider" />
        <div className="row-flex" style={{ gap: 6 }}>
          <input
            className="input"
            placeholder="搜索批次号 / 对照表..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ width: 260 }}
          />
        </div>
        <div className="spacer" />
        <span className="dim mono" style={{ fontSize: 11 }}>
          {data.total} 批次{loading && ' · 加载中…'}
        </span>
        <Link to="/upload" className="btn primary" style={{ textDecoration: 'none' }}>
          <I.upload size={13} /> 上传批次
        </Link>
      </div>
      <div style={{ flex: 1, overflow: 'auto' }}>
        {error && (
          <div style={{ padding: 12, background: 'var(--fail-soft)', color: 'var(--fail)', margin: 12 }}>
            {error}
          </div>
        )}
        <Link to="/trash" className="btn">回收站</Link>
        <Link to="/settings?target=server">连接与同步设置</Link>
        <table className="dtable" style={{ background: 'var(--bg-panel)' }}>
          <thead>
            <tr>
              <th>批次号</th>
              <th>对照表</th>
              <th className="num">器件数</th>
              <th>fs 范围</th>
              <th>去嵌</th>
              <th>处理类型</th>
              <th>上传时间</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && !loading && (
              <tr>
                <td colSpan={8} className="dim" style={{ textAlign: 'center', padding: 24 }}>
                  暂无批次
                </td>
              </tr>
            )}
            {filtered.map((b) => (
              <BatchRow key={b.batch_no} batch={b} onDelete={onDelete} onRename={setRenameBatchNo} />
            ))}
          </tbody>
        </table>

        {renameBatchNo && (
          <RenameBatchModal
            batchNo={renameBatchNo}
            onClose={() => setRenameBatchNo(null)}
            onSubmit={(newBatchNo) => handleRename(renameBatchNo, newBatchNo)}
          />
        )}

        <div style={{ padding: 12, display: 'flex', gap: 6, alignItems: 'center', justifyContent: 'center' }}>
          <button className="btn sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
            <I.chevron size={11} style={{ transform: 'scaleX(-1)' }} /> 上一页
          </button>
          <span className="dim mono" style={{ fontSize: 11 }}>
            {data.page} / {Math.max(1, Math.ceil(data.total / data.size))}
          </span>
          <button
            className="btn sm"
            disabled={page * size >= data.total}
            onClick={() => setPage((p) => p + 1)}
          >
            下一页 <I.chevron size={11} />
          </button>
        </div>
      </div>
    </>
  );
}
