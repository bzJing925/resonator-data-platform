import React, { useEffect, useState } from 'react';

const isElectron = () => !!(window.electronAPI);

interface BootGateProps {
  children?: React.ReactNode;
}

// 等待主进程的就绪标记（IPC），不直接请求后端：
// 后端未监听端口时，每个失败的 HTTP 请求都会被 Chromium 无条件打进控制台
// （ERR_CONNECTION_REFUSED，JS 层无法抑制），冷启动几十秒会刷几百条报错。
// 主进程自身有 TCP 级等待与状态广播，渲染进程只需跟它对齐。
const IPC_POLL_INTERVAL_MS = 500;
// 约 15 秒（30 次轮询）后提示，避免一直 silent wait
const SLOW_HINT_AFTER_POLLS = 30;

export default function BootGate({ children }: BootGateProps) {
  const [ready, setReady] = useState(() => !isElectron());
  const [error, setError] = useState<string | null>(null);
  const [dots, setDots] = useState('');

  useEffect(() => {
    if (!isElectron()) return;

    let cancelled = false;
    let polls = 0;
    let pollTimer: number | undefined;

    const checkMain = async (): Promise<boolean> => {
      try {
        const ok = await window.electronAPI?.isBackendReady?.();
        if (ok && !cancelled) {
          setReady(true);
          return true;
        }
      } catch {
        // ignore
      }
      return false;
    };

    checkMain().then((ok) => {
      if (ok || cancelled) return;
      pollTimer = window.setInterval(async () => {
        polls += 1;
        if (polls === SLOW_HINT_AFTER_POLLS && !cancelled) {
          setError('本地数据服务启动较慢，请继续等待…');
        }
        const done = await checkMain();
        if (done && pollTimer !== undefined) window.clearInterval(pollTimer);
      }, IPC_POLL_INTERVAL_MS);
    });

    // 同时监听主进程状态广播（backend:state），就绪即放行
    window.electronAPI?.onBackendStateChange?.((state) => {
      if (state === 'ready' && !cancelled) setReady(true);
    });

    return () => {
      cancelled = true;
      if (pollTimer !== undefined) window.clearInterval(pollTimer);
    };
  }, []);

  // 加载动画点点
  useEffect(() => {
    if (ready) return;
    const id = setInterval(() => setDots((d) => (d.length >= 3 ? '' : d + '.')), 500);
    return () => clearInterval(id);
  }, [ready]);

  if (ready) return children;

  return (
    <div
      style={{
        width: '100vw',
        height: '100vh',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'var(--bg-app)',
        color: 'var(--fg-2)',
        fontFamily: 'var(--font-sans)',
      }}
    >
      <div style={{ position: 'relative', width: 120, height: 120, marginBottom: 28 }}>
        <svg viewBox="0 0 120 120" style={{ width: '100%', height: '100%', animation: 'spin 3s linear infinite' }}>
          <circle cx="60" cy="60" r="54" fill="none" stroke="#2A2E36" strokeWidth="2" />
          <path
            d="M 60 6 A 54 54 0 0 1 114 60"
            fill="none"
            stroke="#5FA8D3"
            strokeWidth="3"
            strokeLinecap="round"
          />
        </svg>
      </div>
      <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--fg-1)', marginBottom: 8 }}>
        正在启动本地数据服务{dots}
      </div>
      <div style={{ fontSize: 12, color: 'var(--fg-3)' }}>
        ALN 谐振器数据平台
      </div>
      <div style={{ marginTop: 8, fontSize: 11, color: 'var(--fg-3)', maxWidth: 320, textAlign: 'center' }}>
        首次启动或版本更新后需要初始化本地数据服务，耗时较长；之后启动只需几秒
      </div>
      {error && (
        <div style={{ marginTop: 16, fontSize: 12, color: 'var(--warn)', maxWidth: 320, textAlign: 'center' }}>
          {error}
        </div>
      )}
    </div>
  );
}
