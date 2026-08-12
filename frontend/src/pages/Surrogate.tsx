import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import I from '../components/Icons';
import { LineChart } from '../components/Charts';
import { getSurrogateStatus, predictSurrogate } from '../api/endpoints';
import type { SurrogatePredictResponse, SurrogateStatus } from '../types';

// 性能参数列（与后端 surr_param_cols 顺序一致）
const PARAM_LABELS: [string, string, number][] = [
  ['fs_ghz', 'fs (GHz)', 5],
  ['fp_ghz', 'fp (GHz)', 5],
  ['k2eff_pct', 'kt² (%)', 4],
  ['qs', 'Qs', 5],
  ['qp', 'Qp', 5],
  ['qs_bodeq', 'Qs bodeQ', 5],
  ['qp_bodeq', 'Qp bodeQ', 5],
];

const DEBOUNCE_MS = 150;

export default function Surrogate() {
  const [status, setStatus] = useState<SurrogateStatus | null>(null);
  const [eg, setEg] = useState(1.0);
  const [fl, setFl] = useState(0.5);
  const [ag, setAg] = useState(1.0);
  const [area, setArea] = useState(1500);
  const [pf, setPf] = useState(0);
  const [showBase, setShowBase] = useState(true);
  const [showPts, setShowPts] = useState(true);
  const [showTrue, setShowTrue] = useState(true);
  const [data, setData] = useState<SurrogatePredictResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const reqId = useRef(0);

  useEffect(() => {
    getSurrogateStatus()
      .then((s) => {
        setStatus(s);
        if (s.areas?.length) setArea(s.areas[Math.floor(s.areas.length / 2)]);
      })
      .catch(() => setStatus({ available: false, reason: '接口请求失败' }));
  }, []);

  // 生成频谱：去抖 + 过期请求丢弃（速度优先：命中缓存的后端 ~10ms 响应）
  useEffect(() => {
    if (!status?.available) return;
    const id = ++reqId.current;
    setLoading(true);
    const timer = setTimeout(() => {
      predictSurrogate({ eg, fl, area, pf, ag })
        .then((r) => {
          if (reqId.current === id) setData(r);
        })
        .catch(() => {})
        .finally(() => {
          if (reqId.current === id) setLoading(false);
        });
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [status?.available, eg, fl, area, pf, ag]);

  const series = useMemo(() => {
    if (!data) return [];
    const s = [];
    if (showTrue && data.true_curves) {
      for (const [i, t] of data.true_curves.entries()) {
        s.push({
          x: data.freq,
          y: t,
          name: i === 0 ? '同格真值' : undefined,
          color: '#16a34a',
          width: 1,
          opacity: 0.55,
          mode: 'lines',
        });
      }
    }
    if (showBase) {
      s.push({ x: data.freq, y: data.z_base, name: 'B0 基准', color: '#9ca3af', width: 1.2, mode: 'lines', dash: 'dash' });
    }
    s.push({ x: data.freq, y: data.z, name: '代理模型', color: '#60a5fa', width: 2.2, mode: 'lines' });
    if (showPts) {
      s.push({ x: data.sample_freq, y: data.sample_z, name: '稀疏点 (Stage 1)', color: '#ea580c', mode: 'markers' });
    }
    return s;
  }, [data, showBase, showPts, showTrue]);

  const num = useCallback((v: number | undefined, digits: number) => (v == null ? '—' : v.toPrecision(digits)), []);

  if (status && !status.available) {
    return (
      <div style={{ padding: 40, color: 'var(--fg-2)' }}>
        <h2 style={{ fontSize: 18, marginBottom: 12 }}>代理模型</h2>
        <div className="dim" style={{ fontSize: 13 }}>
          模型产物未部署（surrogate_data / checkpoints_surrogate 缺失）。
          <br />
          <span style={{ fontSize: 11, color: 'var(--fg-3)' }}>{status.reason}</span>
        </div>
      </div>
    );
  }

  const inputStyle: React.CSSProperties = {
    width: 76,
    fontSize: 12,
    padding: '2px 6px',
    background: 'var(--bg-3)',
    border: '1px solid var(--border)',
    color: 'var(--fg-1)',
    borderRadius: 3,
  };
  const labelStyle: React.CSSProperties = { fontSize: 11, color: 'var(--fg-3)', marginBottom: 2 };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '10px 14px', gap: 10 }}>
      <div className="toolbar" style={{ display: 'flex', gap: 18, alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <div>
          <div style={labelStyle}>EG（{eg.toFixed(2)}）</div>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <input type="range" min={status?.eg_min ?? 0} max={status?.eg_max ?? 2} step={0.05}
              value={eg} onChange={(e) => setEg(+e.target.value)} style={{ width: 150 }} />
            <input type="number" style={inputStyle} step={0.05} value={eg}
              min={status?.eg_min} max={status?.eg_max}
              onChange={(e) => setEg(+e.target.value)} />
          </div>
        </div>
        <div>
          <div style={labelStyle}>FL（{fl.toFixed(2)}）</div>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <input type="range" min={status?.fl_min ?? 0} max={status?.fl_max ?? 2} step={0.05}
              value={fl} onChange={(e) => setFl(+e.target.value)} style={{ width: 150 }} />
            <input type="number" style={inputStyle} step={0.05} value={fl}
              min={status?.fl_min} max={status?.fl_max}
              onChange={(e) => setFl(+e.target.value)} />
          </div>
        </div>
        <div>
          <div style={labelStyle}>AG (μm)</div>
          <input type="number" style={inputStyle} step={0.1} value={ag} onChange={(e) => setAg(+e.target.value)} />
        </div>
        <div>
          <div style={labelStyle}>面积 (μm²)</div>
          <select style={inputStyle} value={area} onChange={(e) => setArea(+e.target.value)}>
            {(status?.areas ?? []).map((a) => (
              <option key={a} value={a}>{a}</option>
            ))}
          </select>
        </div>
        <div>
          <div style={labelStyle}>PF (massloading)</div>
          <select style={inputStyle} value={pf} onChange={(e) => setPf(+e.target.value)}>
            <option value={0}>无</option>
            <option value={1}>有</option>
          </select>
        </div>
        <div style={{ display: 'flex', gap: 14, fontSize: 12, color: 'var(--fg-2)', paddingBottom: 3 }}>
          <label><input type="checkbox" checked={showBase} onChange={(e) => setShowBase(e.target.checked)} /> B0 基准</label>
          <label><input type="checkbox" checked={showPts} onChange={(e) => setShowPts(e.target.checked)} /> 稀疏点</label>
          <label><input type="checkbox" checked={showTrue} onChange={(e) => setShowTrue(e.target.checked)} /> 同格真值</label>
        </div>
        {loading && <I.spinner size={14} style={{ animation: 'spin 1s linear infinite', color: 'var(--fg-3)' }} />}
      </div>

      {data && !data.in_grid && (
        <div style={{ fontSize: 12, color: '#d97706' }}>
          ⚠ 该结构组合不在训练网格内（或未流片），结果为外推/插值
        </div>
      )}

      <div style={{ flex: 1, minHeight: 380 }}>
        {series.length > 0 && (
          <LineChart series={series} xLabel="Frequency (GHz)" yLabel="|Z11| (dB)" showLegend />
        )}
      </div>

      {data && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr style={{ color: 'var(--fg-3)' }}>
                <th style={{ textAlign: 'left', padding: '3px 10px' }}>指标</th>
                {PARAM_LABELS.map(([k, label]) => (
                  <th key={k} style={{ padding: '3px 10px', textAlign: 'right' }}>{label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                <td style={{ padding: '3px 10px', color: 'var(--fg-2)' }}>预测</td>
                {PARAM_LABELS.map(([k, , d]) => (
                  <td key={k} className="mono" style={{ padding: '3px 10px', textAlign: 'right', color: 'var(--fg-1)' }}>
                    {num(data.params[k], d)}
                  </td>
                ))}
              </tr>
              {data.true_mean && (
                <tr>
                  <td style={{ padding: '3px 10px', color: 'var(--fg-2)' }}>同格真值均值</td>
                  {PARAM_LABELS.map(([k, , d]) => (
                    <td key={k} className="mono" style={{ padding: '3px 10px', textAlign: 'right', color: '#4ade80' }}>
                      {num(data.true_mean?.[k], d)}
                    </td>
                  ))}
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
