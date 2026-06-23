import React, { useEffect, useState } from 'react';
import I from './Icons.jsx';
import { LineChart } from './Charts.jsx';
import {
  getDeviceSparam,
  getDeviceBodeq,
  getDeviceSparseSparam,
  downloadDeviceS1p,
} from '../api/endpoints.js';

const TABS = [
  { key: 's11_db', label: 'S11 幅度', yLabel: 'S11 (dB)' },
  { key: 's11_phase', label: 'S11 相位', yLabel: 'S11 相位 (°)' },
  { key: 'z_mag_db', label: '阻抗谱', yLabel: '|Z| (dB)' },
  { key: 'bodeq', label: 'BodeQ', yLabel: 'BodeQ' },
  { key: 'sparse', label: 'Z-稀疏重建', yLabel: 'Z11 (dB)' },
];

export default function DeviceModal({ device, onClose }) {
  const [tab, setTab] = useState('s11_db');
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!device?.id) return;
    setLoading(true);
    setError(null);
    setData(null);
    // cancelled 防止快速切 tab 时旧 fetch 后到、覆盖新 tab 数据：
    // 比如 S11→BodeQ 时 S11 慢返回，会把 data.values 写进 state，
    // 而 tab 已经是 'bodeq'，渲染 raw/smooth/fitted 全 undefined → 空图。
    let cancelled = false;
    let fetcher;
    if (tab === 'bodeq') {
      fetcher = getDeviceBodeq(device.id);
    } else if (tab === 'sparse') {
      fetcher = getDeviceSparseSparam(device.id, 'z_mag_db', '308', 300);
    } else {
      fetcher = getDeviceSparam(device.id, tab);
    }
    fetcher
      .then((d) => { if (!cancelled) setData(d); })
      .catch((e) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [device?.id, tab]);

  if (!device) return null;

  const tabSpec = TABS.find((t) => t.key === tab);

  let plotProps = null;
  let sparseStats = null;
  if (data) {
    if (tab === 'bodeq') {
      const freq = data.freq_ghz || [];
      const series = [];
      if (data.raw) {
        series.push({ x: freq, y: data.raw, name: '原始', color: '#94a3b8', width: 1, opacity: 0.7 });
      }
      if (data.smooth) {
        series.push({ x: freq, y: data.smooth, name: '平滑', color: '#2c79f6', width: 1.6 });
      }
      if (data.fitted) {
        series.push({ x: freq, y: data.fitted, name: '拟合', color: '#c2410c', width: 2.4 });
      }
      const markers = [
        data.fs_ghz != null ? { x: data.fs_ghz, label: 'fs', color: '#0e9488' } : null,
        data.fp_ghz != null ? { x: data.fp_ghz, label: 'fp', color: '#c97a16' } : null,
        data.fbode_ghz != null ? { x: data.fbode_ghz, label: 'fbode', color: '#7b3fe4' } : null,
      ].filter(Boolean);
      plotProps = {
        series,
        showLegend: true,
        xLabel: '频率 (GHz)',
        yLabel: 'BodeQ',
        markers,
      };
    } else if (tab === 'sparse') {
      const freq = data.freq_ghz || [];
      const series = [];
      // 原始曲线
      if (data.values_true) {
        series.push({ x: freq, y: data.values_true, name: '原始', color: '#94a3b8', width: 1, dash: 'dot', opacity: 0.7 });
      }
      // 重建曲线
      if (data.values) {
        series.push({ x: freq, y: data.values, name: '重建', color: '#2c79f6', width: 1.6 });
      }
      // 采样点
      const pts = data.sample_points || [];
      if (pts.length > 0) {
        series.push({
          x: pts.map((p) => p.freq_ghz),
          y: pts.map((p) => p.z_db),
          name: '采样点',
          mode: 'markers',
          color: '#c2410c',
          width: 0,
        });
      }
      const params = data.params || {};
      const markers = [
        params.fs ? { x: params.fs, label: 'fs', color: '#0e9488' } : null,
        params.fp ? { x: params.fp, label: 'fp', color: '#c97a16' } : null,
      ].filter(Boolean);
      // 区域背景色（后端返回的是整数索引数组，需先转成 Set）
      const extraShapes = [];
      const regions = data.regions || {};
      const regionColors = { main: 'rgba(14,148,136,0.06)', spurious: 'rgba(201,122,22,0.05)' };
      Object.entries(regionColors).forEach(([key, color]) => {
        const indices = regions[key];
        if (!indices || indices.length === 0) return;
        const idxSet = new Set(indices);
        const n = freq.length;
        let inRegion = false;
        let startIdx = 0;
        for (let i = 0; i <= n; i++) {
          if (i < n && idxSet.has(i)) {
            if (!inRegion) { inRegion = true; startIdx = i; }
          } else if (inRegion) {
            extraShapes.push({
              type: 'rect',
              x0: freq[startIdx],
              x1: freq[i - 1],
              yref: 'paper',
              y0: 0,
              y1: 1,
              fillcolor: color,
              line: { width: 0 },
              layer: 'below',
            });
            inRegion = false;
          }
        }
      });
      plotProps = {
        series,
        showLegend: true,
        xLabel: '频率 (GHz)',
        yLabel: 'Z11 (dB)',
        markers,
        extraShapes,
      };
      // 统计信息
      const mainCount = pts.filter((p) => p.region === 'main').length;
      const spurCount = pts.filter((p) => p.region === 'spurious').length;
      const smoothCount = pts.filter((p) => p.region === 'smooth').length;
      sparseStats = { total: pts.length, main: mainCount, spurious: spurCount, smooth: smoothCount, rmse: data.rmse };
    } else {
      plotProps = {
        x: data.freq_ghz || [],
        y: data.values || [],
        xLabel: '频率 (GHz)',
        yLabel: tabSpec.yLabel,
      };
    }
  }

  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" style={{ width: 920 }} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <I.curve size={14} style={{ marginRight: 8, color: 'var(--primary)' }} />
          <span>器件曲线</span>
          <span className="mono dim" style={{ marginLeft: 10, fontSize: 11.5 }}>
            #{device.id} · {device.batch_no || device.batch || '—'}/W{device.wafer} · {device.coord || '—'}
          </span>
          <button
            className="btn ghost sm"
            onClick={() => setTab('z_mag_db')}
            title="查看阻抗谱"
            style={{ marginLeft: 'auto', marginRight: 8 }}
          >
            <I.curve size={13} />
          </button>
          <button
            className="btn ghost sm"
            onClick={async () => {
              try {
                const res = await downloadDeviceS1p(device.id);
                const filename = device.original_filename || `D${device.id}.s1p`;
                const url = window.URL.createObjectURL(res.data);
                const a = document.createElement('a');
                a.href = url;
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                a.remove();
                window.URL.revokeObjectURL(url);
              } catch (e) {
                setError(e.message || '下载 S 参数文件失败');
              }
            }}
            title="下载原始 S 参数文件"
            style={{ marginRight: 8 }}
          >
            <I.download size={13} />
          </button>
          <span className="x" onClick={onClose} style={{ cursor: 'pointer' }}>
            <I.x size={16} />
          </span>
        </div>
        <div className="modal-body" style={{ padding: 0 }}>
          <div className="toolbar" style={{ borderBottom: '1px solid var(--border)' }}>
            <div className="group">
              {TABS.map((t) => (
                <button
                  key={t.key}
                  className={tab === t.key ? 'active' : ''}
                  onClick={() => setTab(t.key)}
                >
                  {t.label}
                </button>
              ))}
            </div>
            <div className="spacer" />
            {data?.file_path && (
              <span className="mono dim" style={{ fontSize: 11 }}>
                {data.file_path}
              </span>
            )}
          </div>
          <div style={{ height: 380, padding: 12 }}>
            {loading && <div className="dim" style={{ padding: 40, textAlign: 'center' }}>加载中…</div>}
            {error && (
              <div style={{ padding: 14, color: 'var(--fail)' }}>
                <I.alert size={12} /> {error}
              </div>
            )}
            {plotProps && <LineChart {...plotProps} />}
          </div>
          <div className="stat-strip" style={{ borderTop: '1px solid var(--border)' }}>
            {tab === 'sparse' && sparseStats ? (
              <>
                <div className="stat">
                  <div className="l">采样点</div>
                  <div className="v">{sparseStats.total}</div>
                </div>
                <div className="stat">
                  <div className="l">主/杂/平</div>
                  <div className="v" style={{ fontSize: 11 }}>
                    {sparseStats.main}/{sparseStats.spurious}/{sparseStats.smooth}
                  </div>
                </div>
                <div className="stat">
                  <div className="l">均方根误差</div>
                  <div className="v">
                    {sparseStats.rmse != null ? sparseStats.rmse.toFixed(3) : '—'}
                    <span className="u">dB</span>
                  </div>
                </div>
                <div className="stat">
                  <div className="l">kt²</div>
                  <div className="v">
                    {(data?.params?.kt2)?.toFixed(2) || '—'}
                    <span className="u">%</span>
                  </div>
                </div>
              </>
            ) : (
              <>
                <div className="stat">
                  <div className="l">串联谐振 fs</div>
                  <div className="v">
                    {device.fs_ghz?.toFixed(3) || '—'}
                    <span className="u">GHz</span>
                  </div>
                </div>
                <div className="stat">
                  <div className="l">并联谐振 fp</div>
                  <div className="v">
                    {device.fp_ghz?.toFixed(3) || '—'}
                    <span className="u">GHz</span>
                  </div>
                </div>
                <div className="stat">
                  <div className="l">串联 Qs</div>
                  <div className="v">{device.qs?.toFixed(0) || '—'}</div>
                </div>
                <div className="stat">
                  <div className="l">有效耦合 k²eff</div>
                  <div className="v">
                    {device.k2eff_pct?.toFixed(2) || '—'}
                    <span className="u">%</span>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
