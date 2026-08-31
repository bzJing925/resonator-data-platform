import React, { memo, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import I from '../components/Icons';
import { getStats, listTasks } from '../api/endpoints';
import type { StatsResponse, Task } from '../types';

/* -------------------------------------------------------------------------
 * SmithChart — signature visual: a slowly rotating Smith chart / resonator
 * ring that embodies the RF/microwave world of the product.
 * ----------------------------------------------------------------------- */
function SmithChart() {
  return (
    <div style={{ position: 'relative', width: 320, height: 320 }}>
      <svg viewBox="0 0 200 200" style={{ width: '100%', height: '100%', animation: 'spin 60s linear infinite' }}>
        <defs>
          <radialGradient id="smithGlow" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="rgba(95, 168, 211, 0.08)" />
            <stop offset="100%" stopColor="rgba(95, 168, 211, 0)" />
          </radialGradient>
        </defs>
        <circle cx="100" cy="100" r="90" fill="url(#smithGlow)" />
        <circle cx="100" cy="100" r="90" fill="none" stroke="#2A2E36" strokeWidth="1" />
        <circle cx="100" cy="100" r="60" fill="none" stroke="#2A2E36" strokeWidth="0.5" />
        <circle cx="100" cy="100" r="30" fill="none" stroke="#2A2E36" strokeWidth="0.5" />
        {/* resistance circles */}
        <circle cx="145" cy="100" r="45" fill="none" stroke="#3A4049" strokeWidth="0.5" />
        <circle cx="55" cy="100" r="45" fill="none" stroke="#3A4049" strokeWidth="0.5" />
        <circle cx="100" cy="55" r="45" fill="none" stroke="#3A4049" strokeWidth="0.5" />
        <circle cx="100" cy="145" r="45" fill="none" stroke="#3A4049" strokeWidth="0.5" />
        {/* central trace */}
        <path
          d="M 190 100 Q 150 70 120 95 T 85 110 T 55 95 T 30 105"
          fill="none"
          stroke="#5FA8D3"
          strokeWidth="2"
          strokeLinecap="round"
        />
        <circle cx="30" cy="105" r="3" fill="#E8C547" />
      </svg>
    </div>
  );
}

interface TileProps {
  label: string;
  value: React.ReactNode;
  unit?: string;
  sub?: React.ReactNode;
  accent?: string;
}

const Tile = memo(function Tile({ label, value, unit, sub, accent }: TileProps) {
  return (
    <div
      style={{
        background: 'var(--bg-panel)',
        border: '1px solid var(--border)',
        borderRadius: 4,
        padding: '12px 14px',
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: 3, background: accent || 'var(--primary)' }} />
      <div style={{ fontSize: 10.5, textTransform: 'uppercase', letterSpacing: 0.5, color: 'var(--fg-3)', marginBottom: 6 }}>
        {label}
      </div>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 26, fontWeight: 600, color: 'var(--fg-1)', lineHeight: 1.1 }}>
        {value}
        <span style={{ fontSize: 13, color: 'var(--fg-4)', marginLeft: 4 }}>{unit}</span>
      </div>
      {sub && (
        <div style={{ fontSize: 11, color: 'var(--fg-3)', marginTop: 4, fontFamily: 'var(--font-mono)' }}>
          {sub}
        </div>
      )}
    </div>
  );
});

interface StatusBadgeProps {
  status?: string;
}

const StatusBadge = memo(function StatusBadge({ status }: StatusBadgeProps) {
  const map: Record<string, [string, string]> = {
    running: ['run', '运行中'],
    success: ['done', '成功'],
    failed: ['err', '失败'],
    error: ['err', '错误'],
    pending: ['idle', '排队中'],
  };
  const [cls, txt] = map[status || ''] || ['idle', String(status || '').toUpperCase()];
  return <span className={`badge ${cls}`}>{txt}</span>;
});

export default function Dashboard() {
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([getStats(), listTasks()])
      .then(([s, t]) => {
        setStats(s);
        setTasks(Array.isArray(t) ? t : (t as { items?: Task[] })?.items || []);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  const fmtN = (n?: number | null) => (n == null ? '—' : n.toLocaleString());

  return (
    <>
      <div className="toolbar">
        <span className="crumb">
          <b>仪表盘</b>
        </span>
        <div className="spacer" />
        <button className="btn ghost" onClick={() => window.location.reload()}>
          <I.refresh size={13} /> 刷新
        </button>
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: 14 }}>
        {error && (
          <div style={{ padding: 12, background: 'var(--fail-soft)', border: '1px solid var(--fail)', borderRadius: 4, marginBottom: 12 }}>
            <I.alert size={14} /> {error}
          </div>
        )}

        {/* Hero: signature Smith chart + headline */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 320px',
            gap: 20,
            alignItems: 'center',
            background: 'linear-gradient(135deg, #181B21 0%, #13161B 100%)',
            border: '1px solid var(--border)',
            borderRadius: 6,
            padding: '28px 32px',
            marginBottom: 14,
            overflow: 'hidden',
            position: 'relative',
          }}
        >
          <div style={{ position: 'relative', zIndex: 1 }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, letterSpacing: 1.5, color: 'var(--primary)', textTransform: 'uppercase', marginBottom: 10 }}>
              ALN 谐振器测试数据平台
            </div>
            <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 36, fontWeight: 700, color: 'var(--fg-1)', margin: 0, lineHeight: 1.15, letterSpacing: '-0.02em' }}>
              谐振器数据工作台
            </h1>
            <p style={{ fontSize: 14, color: 'var(--fg-3)', maxWidth: 520, marginTop: 12, lineHeight: 1.6 }}>
              上传 S 参数文件，自动去嵌、提取五参数、可视化 wafer 分布与阻抗谱。
              所有数据本地保存，无需离开桌面。
            </p>
            <div style={{ display: 'flex', gap: 10, marginTop: 22 }}>
              <Link to="/upload" className="btn primary" style={{ height: 32, padding: '0 16px', textDecoration: 'none' }}>
                <I.upload size={14} /> 上传数据
              </Link>
              <Link to="/explore" className="btn" style={{ height: 32, padding: '0 16px', textDecoration: 'none' }}>
                探索分析
              </Link>
            </div>
          </div>
          <div style={{ position: 'relative', zIndex: 1, opacity: 0.9 }}>
            <SmithChart />
          </div>
          <div
            style={{
              position: 'absolute',
              right: -80,
              top: -80,
              width: 400,
              height: 400,
              borderRadius: '50%',
              background: 'radial-gradient(circle, rgba(95,168,211,0.08) 0%, transparent 70%)',
              pointerEvents: 'none',
            }}
          />
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 10, marginBottom: 14 }}>
          <Tile label="批次总数" value={fmtN(stats?.batches)} unit="" />
          <Tile label="器件记录" value={fmtN(stats?.devices)} unit="" accent="var(--t3)" />
          <Tile label="对照表" value={fmtN(stats?.mappings)} unit="" accent="var(--t5)" />
          <Tile label="磁盘使用" value={stats?.disk_used_gb?.toFixed(1) || '—'} unit="GB" sub={`${fmtN(stats?.disk_free_gb)} GB 可用`} accent="var(--t2)" />
          <Tile label="进行中任务" value={fmtN(stats?.tasks_running)} unit="" accent="var(--running)" />
          <Tile label="排队任务" value={fmtN(stats?.tasks_pending)} unit="" accent="var(--warn)" />
        </div>

        <div style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 4 }}>
          <div className="panel-head" style={{ borderRadius: '4px 4px 0 0' }}>
            <I.cpu size={12} />
            <span>最近任务</span>
            <Link to="/tasks" className="btn ghost sm" style={{ marginLeft: 'auto', height: 22, textDecoration: 'none' }}>
              查看全部 ›
            </Link>
          </div>
          <table className="dtable">
            <thead>
              <tr>
                <th>任务 ID</th>
                <th>批次号</th>
                <th>状态</th>
                <th style={{ width: 240 }}>进度</th>
                <th>开始</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {tasks.length === 0 && (
                <tr>
                  <td colSpan={6} className="dim" style={{ textAlign: 'center', padding: 24 }}>
                    暂无任务
                  </td>
                </tr>
              )}
              {tasks.slice(0, 10).map((t) => (
                <tr key={t.id}>
                  <td className="mono">{String(t.id).slice(0, 8)}</td>
                  <td>
                    <b style={{ color: 'var(--fg-1)' }}>{t.batch_no}</b>
                  </td>
                  <td>
                    <StatusBadge status={t.status} />
                  </td>
                  <td>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <div style={{ flex: 1, height: 5, background: 'var(--bg-panel-2)', border: '1px solid var(--border)', borderRadius: 3, overflow: 'hidden' }}>
                        <div
                          style={{
                            width: `${t.progress_pct || 0}%`,
                            height: '100%',
                            background:
                              t.status === 'failed' || t.status === 'error'
                                ? 'var(--fail)'
                                : t.status === 'success'
                                ? 'var(--pass)'
                                : 'var(--primary)',
                            transition: 'width 0.3s',
                          }}
                        />
                      </div>
                      <span style={{ width: 40, textAlign: 'right' }}>{t.progress_pct || 0}%</span>
                    </div>
                  </td>
                  <td className="mono dim" style={{ fontSize: 11 }}>
                    {t.started_at ? new Date(t.started_at).toLocaleString() : '—'}
                  </td>
                  <td>
                    <Link to={`/tasks/${t.id}`} className="btn ghost sm" style={{ textDecoration: 'none' }}>
                      <I.more size={12} />
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
