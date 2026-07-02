import React, { memo, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import I from '../components/Icons.jsx';
import { listBatches, deleteBatch } from '../api/endpoints.js';

const BatchRow = memo(function BatchRow({ batch, onDelete }) {
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
          <button className="btn ghost sm danger" title="删除" onClick={() => onDelete(b.batch_no)}>
            <I.trash size={12} />
          </button>
        </div>
      </td>
    </tr>
  );
});

export default function Batches() {
  const [page, setPage] = useState(1);
  const [size] = useState(20);
  const [data, setData] = useState({ items: [], total: 0, page: 1, size: 20 });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState('');

  // load 接受外部 cancelled flag —— useEffect 重跑时能让旧请求丢弃结果，
  // 避免分页/size 快速切换时慢请求覆盖快请求结果。
  const load = (cancelled = { current: false }) => {
    setLoading(true);
    listBatches({ page, size, sort: '-uploaded_at' })
      .then((d) => { if (!cancelled.current) setData(d); })
      .catch((e) => { if (!cancelled.current) setError(e.message); })
      .finally(() => { if (!cancelled.current) setLoading(false); });
  };

  useEffect(() => {
    const cancelled = { current: false };
    load(cancelled);
    return () => { cancelled.current = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, size]);

  const onDelete = async (batchNo) => {
    if (!confirm(`确认删除批次 ${batchNo}？此操作不可逆。`)) return;
    try {
      await deleteBatch(batchNo);
      load();
    } catch (e) {
      alert(e.message);
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
      <div className="toolbar">
        <span className="crumb">
          谐振器 <span style={{ color: 'var(--fg-4)' }}>›</span> <b>批次管理</b>
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
                <td colSpan="8" className="dim" style={{ textAlign: 'center', padding: 24 }}>
                  暂无批次
                </td>
              </tr>
            )}
            {filtered.map((b) => (
              <BatchRow key={b.batch_no} batch={b} onDelete={onDelete} />
            ))}
          </tbody>
        </table>

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
