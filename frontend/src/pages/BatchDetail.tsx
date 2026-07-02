import React, { memo, useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import I from '../components/Icons';
import {
  getBatch,
  listBatchDevices,
  listBatchFiles,
  exportCsv,
  downloadFilesZip,
  downloadDeviceS1p,
} from '../api/endpoints';
import DeviceModal from '../components/DeviceModal';
import useFields, { displayLabel } from '../hooks/useFields';
import type { Batch, Device, FileEntry } from '../types';

interface DeviceRowProps {
  device: Device;
  columns: ColumnDef[];
  fmtCell: (d: Device, c: ColumnDef) => React.ReactNode;
  onRowClick: (d: Device) => void;
  onDownload: (d: Device) => void;
}

interface ColumnDef {
  key: string;
  fallback: string;
  type: string;
  digits?: number;
  header?: string;
  render?: (d: Device) => React.ReactNode;
}

const DeviceRow = memo(function DeviceRow({ device, columns, fmtCell, onRowClick, onDownload }: DeviceRowProps) {
  const d = device;
  return (
    <tr style={{ cursor: 'pointer' }} onClick={() => onRowClick(d)}>
      <td className="mono">{d.id || '—'}</td>
      {columns.map((c) => (
        <td key={c.key} className={c.type === 'num' ? 'num mono' : ''}>{fmtCell(d, c)}</td>
      ))}
      <td>
        <button className="btn ghost sm" onClick={(e) => { e.stopPropagation(); onRowClick(d); }} title="查看曲线">
          <I.curve size={12} />
        </button>
        <button
          className="btn ghost sm"
          onClick={(e) => { e.stopPropagation(); onDownload(d); }}
          title="下载原始 S 参数文件"
        >
          <I.download size={12} />
        </button>
      </td>
    </tr>
  );
});

// 表格列定义（按"标识 → 工艺 → 主参数 → BodeQ → 中间峰"分组）
// type: 'text' | 'num'  (num 用 mono 等宽字体右对齐)
// digits: 数值精度
const COLUMN_DEFS: ColumnDef[] = [
  // 标识
  { key: 'original_filename', fallback: '原始文件名', type: 'text' },
  { key: 'mark', fallback: '标记 Mark', type: 'text' },
  { key: 'wafer', fallback: '晶圆 Wafer', type: 'text', render: (d) => (d.wafer != null ? `W${d.wafer}` : '—') },
  { key: 'coord', fallback: '坐标 Coord', type: 'text' },
  { key: 'x', fallback: 'X', type: 'num', digits: 0 },
  { key: 'y', fallback: 'Y', type: 'num', digits: 0 },
  { key: 'pf', fallback: '合格 P/F', type: 'text', render: (d) => <span className={d.pf === 'Y' ? 'pass' : 'fail'}>{d.pf || '—'}</span> },
  // 工艺
  { key: 'eg', fallback: '电极间隙 EG', type: 'num', digits: 2 },
  { key: 'fl', fallback: '指长 FL', type: 'num', digits: 2 },
  { key: 'ag', fallback: '孔径 AG', type: 'num', digits: 2 },
  { key: 'area_um2', fallback: '面积 (μm²)', type: 'num', digits: 0 },
  // 主参数
  { key: 'fs_ghz', fallback: '串联谐振 fs (GHz)', type: 'num', digits: 4 },
  { key: 'fp_ghz', fallback: '并联谐振 fp (GHz)', type: 'num', digits: 4 },
  { key: 'zs_ohm', fallback: '串联阻抗 Zs (Ω)', type: 'num', digits: 2 },
  { key: 'zp_ohm', fallback: '并联阻抗 Zp (Ω)', type: 'num', digits: 2 },
  { key: 'qs', fallback: '串联 Qs', type: 'num', digits: 0 },
  { key: 'qp', fallback: '并联 Qp', type: 'num', digits: 0 },
  // BodeQ
  { key: 'fbode_ghz', fallback: 'BodeQ 频率 (GHz)', type: 'num', digits: 4 },
  { key: 'qs_bodeq', fallback: 'BodeQ 串联 Qs', type: 'num', digits: 0 },
  { key: 'qp_bodeq', fallback: 'BodeQ 并联 Qp', type: 'num', digits: 0 },
  { key: 'k2eff_pct', fallback: '有效机电耦合 k²eff (%)', type: 'num', digits: 2 },
  // 中间峰
  { key: 'fp2_ghz', fallback: '二次并联 fp2 (GHz)', type: 'num', digits: 4 },
  { key: 'fs2_ghz', fallback: '二次串联 fs2 (GHz)', type: 'num', digits: 4 },
];

// 触发浏览器下载一个 Blob
function downloadBlob(blob: Blob, filename: string) {
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}

// 完整导出字段（覆盖 Device 表所有列 + virtual batch_no）
const EXPORT_FIELDS = [
  'id', 'batch_no',
  'original_filename', 'display_name', 'mark', 'wafer', 'folder_name', 'coord', 'x', 'y',
  'eg', 'fl', 'ag', 'pf', 'area_n', 'area_um2',
  'fs_ghz', 'fp_ghz', 'zs_ohm', 'zp_ohm', 'qs', 'qp',
  'qs_bodeq', 'qp_bodeq', 'dbqs', 'dbqp',
  'bodeq_fitted', 'bodeq_smooth', 'bodeq_raw', 'fbode_ghz', 'k2eff_pct',
  'fp2_ghz', 'fs2_ghz', 'zp2_ohm', 'zs2_ohm',
  'deembedded', 's_param_path',
];

export default function BatchDetail() {
  const { batchNo } = useParams<{ batchNo: string }>();
  const [detail, setDetail] = useState<Batch | null>(null);
  const [devices, setDevices] = useState<{ items: Device[]; total: number }>({ items: [], total: 0 });
  const [page, setPage] = useState<number>(1);
  const [size] = useState<number>(50);
  const [waferFilter, setWaferFilter] = useState<string>('');
  const [pfFilter, setPfFilter] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const [activeDevice, setActiveDevice] = useState<Device | null>(null);
  const [exporting, setExporting] = useState<boolean>(false);
  const [files, setFiles] = useState<FileEntry[]>([]);
  const [selectedFiles, setSelectedFiles] = useState<Set<string>>(new Set());
  const [filesLoading, setFilesLoading] = useState<boolean>(false);
  const fieldsState = useFields();

  // 计算列头：优先用 useFields 的 label+unit，缺失时回退到 fallback
  const columns = useMemo(() => {
    return COLUMN_DEFS.map((c) => {
      const f = fieldsState.data?.byName?.[c.key];
      return { ...c, header: f ? displayLabel(f) : c.fallback };
    });
  }, [fieldsState.data]);

  const fmtCell = useCallback((d: Device, c: ColumnDef) => {
    if (c.render) return c.render(d);
    const v = d[c.key];
    if (v == null || v === '') return '—';
    if (c.type === 'num' && typeof v === 'number') {
      return Number.isFinite(v) ? v.toFixed(c.digits ?? 2) : '—';
    }
    return v as React.ReactNode;
  }, []);

  useEffect(() => {
    if (!batchNo) return;
    let cancelled = false;
    getBatch(batchNo)
      .then((d) => { if (!cancelled) setDetail(d); })
      .catch((e: Error) => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [batchNo]);

  useEffect(() => {
    if (!batchNo) return;
    let cancelled = false;
    setFilesLoading(true);
    listBatchFiles(batchNo, true)
      .then((d) => { if (!cancelled) { setFiles(d || []); setSelectedFiles(new Set()); } })
      .catch((e: Error) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setFilesLoading(false); });
    return () => { cancelled = true; };
  }, [batchNo]);

  useEffect(() => {
    if (!batchNo) return;
    // cancelled 防止快速改 filter 时慢请求后到、覆盖快请求的当前数据：
    // 否则用户连续切 wafer 或翻页时可能看到上一次过滤的结果。
    let cancelled = false;
    const params: Record<string, unknown> = { page, size };
    if (waferFilter) params.wafer = waferFilter;
    if (pfFilter) params.pf = pfFilter;
    listBatchDevices(batchNo, params)
      .then((d) => { if (!cancelled) setDevices(d); })
      .catch((e: Error) => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [batchNo, page, size, waferFilter, pfFilter]);

  const items = devices.items || [];

  const handleRowClick = useCallback((d: Device) => setActiveDevice(d), []);
  const handleCloseDevice = useCallback(() => setActiveDevice(null), []);

  const handleDownloadS1p = useCallback(async (device: Device) => {
    if (!device.id) return;
    try {
      const res = await downloadDeviceS1p(device.id);
      const filename = device.original_filename || `${device.batch_no || 'batch'}_D${device.id}.s1p`;
      downloadBlob(res.data, filename);
    } catch (e: any) {
      setError(e.message || '下载 S 参数文件失败');
    }
  }, []);

  const handleDownloadZip = useCallback(async (relpaths: string[] = []) => {
    if (!batchNo) return;
    try {
      const res = await downloadFilesZip(batchNo, relpaths);
      const suffix = relpaths.length ? 'selected' : 'all';
      downloadBlob(res.data, `${batchNo}_${suffix}.zip`);
    } catch (e: any) {
      setError(e.message || '下载 ZIP 失败');
    }
  }, [batchNo]);

  const toggleFile = useCallback((relpath: string) => {
    setSelectedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(relpath)) next.delete(relpath);
      else next.add(relpath);
      return next;
    });
  }, []);

  const selectAllFiles = useCallback(() => {
    setSelectedFiles(new Set(files.map((f) => f.relpath)));
  }, [files]);

  const clearFileSelection = useCallback(() => {
    setSelectedFiles(new Set());
  }, []);

  const onExportCsv = useCallback(async () => {
    if (!batchNo) return;
    setExporting(true);
    setError(null);
    try {
      const res = await exportCsv({
        filters: { batch_no: [batchNo] },
        fields: EXPORT_FIELDS,
        limit: 200000,
        order_by: 'id',
      });
      const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
      downloadBlob(res.data, `${batchNo}_devices_${ts}.csv`);
    } catch (e: any) {
      setError(e.message || '导出失败');
    } finally {
      setExporting(false);
    }
  }, [batchNo]);

  return (
    <>
      <div className="toolbar">
        <span className="crumb">
          <Link to="/batches" style={{ color: 'inherit', textDecoration: 'none' }}>
            批次管理
          </Link>{' '}
          <span style={{ color: 'var(--fg-4)' }}>›</span> <b>{batchNo}</b>
        </span>
        <div className="spacer" />
        <button className="btn" onClick={onExportCsv} disabled={exporting} title="导出当前批次全部 devices 为 CSV">
          <I.download size={13} /> {exporting ? '导出中…' : '导出 CSV'}
        </button>
        <button className="btn" disabled title="敬请期待">
          <I.download size={13} /> 导出 Excel
        </button>
        <button
          className="btn"
          onClick={() => handleDownloadZip()}
          title="下载本批次全部 snp 文件打包"
        >
          <I.download size={13} /> 下载全部
        </button>
      </div>

      <div style={{ flex: 1, overflow: 'auto', padding: 14 }}>
        {error && (
          <div style={{ padding: 12, background: 'var(--fail-soft)', color: 'var(--fail)', marginBottom: 12 }}>
            {error}
          </div>
        )}
        {detail && (
          <div className="chart-card" style={{ margin: 0, marginBottom: 12 }}>
            <div className="chart-head">
              <span className="title">批次概览</span>
            </div>
            <div style={{ padding: 14, display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
              <Stat label="对照表" value={detail.mapping_name || '—'} />
              <Stat label="器件数" value={(detail.device_count || 0).toLocaleString()} />
              <Stat
                label="fs 范围"
                value={
                  detail.f_start_ghz != null
                    ? `${detail.f_start_ghz} – ${detail.f_end_ghz} GHz`
                    : '全频段'
                }
              />
              <Stat
                label="处理类型"
                value={
                  detail.process_type === 'BOTH'
                    ? '双端口'
                    : detail.process_type === 'S1P'
                    ? 'S1P'
                    : detail.process_type === 'S2P'
                    ? 'S2P'
                    : (detail.process_type || '—')
                }
              />
              <Stat label="去嵌" value={detail.deembedded ? '是' : '否'} />
              <Stat label="Wafer" value={(detail.wafers || []).map((w) => `W${w}`).join(', ') || '—'} />
              <Stat
                label="fs 中位 (GHz)"
                value={detail.stats?.fs_ghz_median != null ? detail.stats.fs_ghz_median.toFixed(3) : '—'}
              />
              <Stat
                label="Pass 率"
                value={
                  detail.stats?.pass_rate != null
                    ? `${(detail.stats.pass_rate * 100).toFixed(1)}%`
                    : '—'
                }
                accent="var(--pass)"
              />
            </div>
          </div>
        )}

        <div className="chart-card" style={{ margin: 0, marginBottom: 12 }}>
          <div className="chart-head">
            <span className="title">源文件列表</span>
            <span className="axes">{filesLoading ? '加载中…' : `${files.length} 个文件`}</span>
            <div className="right">
              <button className="btn sm" onClick={selectAllFiles}>全选</button>
              <button className="btn sm" onClick={clearFileSelection}>清空</button>
              <button
                className="btn sm"
                disabled={selectedFiles.size === 0}
                onClick={() => handleDownloadZip(Array.from(selectedFiles))}
              >
                下载选中 ({selectedFiles.size})
              </button>
            </div>
          </div>
          <div style={{ overflow: 'auto', maxHeight: '30vh' }}>
            <table className="dtable dtable-wide">
              <thead>
                <tr>
                  <th style={{ width: 40 }}><input type="checkbox" readOnly checked={false} style={{ display: 'none' }} /></th>
                  <th>文件名</th>
                  <th>相对路径</th>
                  <th>大小</th>
                </tr>
              </thead>
              <tbody>
                {files.length === 0 && !filesLoading && (
                  <tr>
                    <td colSpan={4} className="dim" style={{ textAlign: 'center', padding: 24 }}>
                      暂无源文件（可能已清理）
                    </td>
                  </tr>
                )}
                {files.map((f) => (
                  <tr key={f.relpath}>
                    <td>
                      <input
                        type="checkbox"
                        checked={selectedFiles.has(f.relpath)}
                        onChange={() => toggleFile(f.relpath)}
                      />
                    </td>
                    <td>{f.name}</td>
                    <td className="mono dim">{f.relpath}</td>
                    <td className="num">{(f.size / 1024 / 1024).toFixed(2)} MB</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="chart-card" style={{ margin: 0 }}>
          <div className="chart-head">
            <span className="title">器件列表</span>
            <span className="axes">{devices.total || 0} 行</span>
            <div className="right">
              <input
                className="input sm"
                placeholder="晶圆"
                value={waferFilter}
                onChange={(e) => {
                  setWaferFilter(e.target.value);
                  setPage(1);
                }}
                style={{ width: 80 }}
              />
              <select
                className="select"
                value={pfFilter}
                onChange={(e) => {
                  setPfFilter(e.target.value);
                  setPage(1);
                }}
              >
                <option value="">全部 P/F</option>
                <option value="Y">通过</option>
                <option value="N">失败</option>
              </select>
            </div>
          </div>
          <div style={{ overflow: 'auto', maxHeight: '60vh' }}>
            <table className="dtable dtable-wide">
              <thead>
                <tr>
                  <th>器件 ID</th>
                  {columns.map((c) => (
                    <th key={c.key} className={c.type === 'num' ? 'num' : ''}>{c.header}</th>
                  ))}
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {items.length === 0 && (
                  <tr>
                    <td colSpan={columns.length + 2} className="dim" style={{ textAlign: 'center', padding: 24 }}>
                      暂无器件
                    </td>
                  </tr>
                )}
                {items.map((d) => (
                  <DeviceRow
                    key={d.id || `${d.wafer}-${d.coord}`}
                    device={d}
                    columns={columns}
                    fmtCell={fmtCell}
                    onRowClick={handleRowClick}
                    onDownload={handleDownloadS1p}
                  />
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ padding: 12, display: 'flex', gap: 6, alignItems: 'center', justifyContent: 'center' }}>
            <button className="btn sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
              <I.chevron size={11} style={{ transform: 'scaleX(-1)' }} />
            </button>
            <span className="dim mono" style={{ fontSize: 11 }}>
              {page} / {Math.max(1, Math.ceil((devices.total || 0) / size))}
            </span>
            <button className="btn sm" disabled={page * size >= (devices.total || 0)} onClick={() => setPage((p) => p + 1)}>
              <I.chevron size={11} />
            </button>
          </div>
        </div>
      </div>

      {activeDevice && <DeviceModal device={activeDevice} onClose={handleCloseDevice} />}
    </>
  );
}

interface StatProps {
  label: string;
  value: React.ReactNode;
  accent?: string;
}

function Stat({ label, value, accent }: StatProps) {
  return (
    <div
      style={{
        background: 'var(--bg-panel-2)',
        border: '1px solid var(--border)',
        borderRadius: 4,
        padding: 10,
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          position: 'absolute',
          left: 0,
          top: 0,
          bottom: 0,
          width: 2,
          background: accent || 'var(--fg-4)',
        }}
      />
      <div style={{ fontSize: 10.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: 0.4 }}>
        {label}
      </div>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 16, fontWeight: 600, color: 'var(--fg-1)', marginTop: 2 }}>
        {value}
      </div>
    </div>
  );
}
