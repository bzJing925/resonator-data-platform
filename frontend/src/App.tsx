import React, { memo, Suspense, useEffect, useState } from 'react';
import { Routes, Route, useLocation } from 'react-router-dom';
import Sidebar from './components/Sidebar';
import ErrorBoundary from './components/ErrorBoundary';
import FloatingUploadProgress from './components/FloatingUploadProgress';
import I from './components/Icons';
import Dashboard from './pages/Dashboard';
import { getHealth } from './api/endpoints';
import { UploadProgressProvider } from './contexts/UploadProgressContext';

// 路由懒加载：非首屏页面按需加载，显著减小初始 bundle
const Upload       = React.lazy(() => import('./pages/Upload'));
const Batches      = React.lazy(() => import('./pages/Batches'));
const BatchDetail  = React.lazy(() => import('./pages/BatchDetail'));
const Mappings     = React.lazy(() => import('./pages/Mappings'));
const Explore      = React.lazy(() => import('./pages/Explore'));
const Tasks        = React.lazy(() => import('./pages/Tasks'));
const TaskDetail   = React.lazy(() => import('./pages/TaskDetail'));
const Impedance    = React.lazy(() => import('./pages/Impedance'));

const PageLoader = memo(function PageLoader() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--fg-3)' }}>
      <I.spinner size={20} style={{ animation: 'spin 1s linear infinite', marginRight: 8 }} />
      加载中…
    </div>
  );
});

const Titlebar = memo(function Titlebar({ health }: { health?: { status?: string; db?: string; redis?: string } }) {
  const dot = health?.status === 'ok' ? '' : ' warn';
  const statusText = {
    ok: '正常',
    degraded: '降级',
    down: '离线',
  }[health?.status] || health?.status || '...';
  const dbText = { ok: '正常', down: '离线' }[health?.db] || health?.db || '...';
  const redisText = { ok: '正常', down: '离线' }[health?.redis] || health?.redis || '...';
  return (
    <div className="titlebar">
      <div className="brand">
        <span className="mark">Σ</span>
        <span className="name" style={{ fontFamily: 'var(--font-display)', letterSpacing: '0.02em' }}>ALN 谐振器数据平台</span>
        <span className="ver">v0.2 · 桌面版</span>
      </div>
      <div className="spacer" />
      <div className="right">
        <span className={`pill${dot}`}>
          <span className="dot" />接口 · {statusText}
        </span>
        <span className={`pill${health?.db === 'ok' ? '' : ' warn'}`}>
          <span className="dot" />数据库 · {dbText}
        </span>
        <span className={`pill${health?.redis === 'ok' ? '' : ' warn'}`}>
          <span className="dot" />缓存 · {redisText}
        </span>
      </div>
    </div>
  );
});

const Statusbar = memo(function Statusbar() {
  const loc = useLocation();
  return (
    <div className="statusbar">
      <span className="seg">
        <span className="dot" />已连接
      </span>
      <span className="seg">PostgreSQL 15 数据库</span>
      <span className="seg">Redis 7 缓存</span>
      <span className="seg">Celery 任务队列</span>
      <span className="spacer" />
      <span className="seg">
        路径：<b style={{ color: '#fff' }}>{loc.pathname}</b>
      </span>
      <span className="seg">时区：Asia/Shanghai</span>
      <span className="seg">© aln-data 2026</span>
    </div>
  );
});

export default function App() {
  const [health, setHealth] = useState<{ status?: string; db?: string; redis?: string } | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = () =>
      getHealth()
        .then((h) => alive && setHealth(h))
        .catch(() => alive && setHealth({ status: 'down', db: 'down', redis: 'down' }));
    tick();
    const id = setInterval(tick, 15000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  return (
    <UploadProgressProvider>
      <div className="app">
        <Titlebar health={health} />
        <Sidebar />
        <div className="main">
          <ErrorBoundary>
            <Suspense fallback={<PageLoader />}>
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/explore" element={<Explore />} />
                <Route path="/batches" element={<Batches />} />
                <Route path="/batches/:batchNo" element={<BatchDetail />} />
                <Route path="/mappings" element={<Mappings />} />
                <Route path="/upload" element={<Upload />} />
                <Route path="/tasks" element={<Tasks />} />
                <Route path="/tasks/:taskId" element={<TaskDetail />} />
                <Route path="/impedance" element={<Impedance />} />
                <Route path="*" element={<NotFound />} />
              </Routes>
            </Suspense>
          </ErrorBoundary>
        </div>
        <Statusbar />
        <FloatingUploadProgress />
      </div>
    </UploadProgressProvider>
  );
}

function NotFound() {
  return (
    <div style={{ padding: 40, textAlign: 'center', color: 'var(--fg-3)' }}>
      <I.alert size={32} />
      <div style={{ marginTop: 12 }}>页面未找到</div>
    </div>
  );
}
