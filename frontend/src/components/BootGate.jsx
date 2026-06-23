import React, { useEffect, useState } from 'react';
import { getHealth } from '../api/endpoints.js';

const isElectron = () => !!(window.electronAPI);

export default function BootGate({ children }) {
  const [ready, setReady] = useState(() => !isElectron());
  const [error, setError] = useState(null);
  const [dots, setDots] = useState('');

  useEffect(() => {
    if (!isElectron()) return;

    let cancelled = false;
    let attempts = 0;

    // 先检查主进程是否已标记后端就绪
    const checkMain = async () => {
      try {
        const ok = await window.electronAPI?.isBackendReady?.();
        if (ok) {
          setReady(true);
          return true;
        }
      } catch {
        // ignore
      }
      return false;
    };

    // 轮询 health 接口
    const pollHealth = async () => {
      while (!cancelled) {
        attempts += 1;
        try {
          await getHealth();
          if (!cancelled) setReady(true);
          return;
        } catch {
          // 第 30 次（约 6 秒）后提示，避免一直 silent fail
          if (attempts === 30 && !cancelled) {
            setError('本地数据服务启动较慢，请继续等待…');
          }
        }
        await new Promise((r) => setTimeout(r, 200));
      }
    };

    checkMain().then((ok) => {
      if (!ok && !cancelled) pollHealth();
    });

    // 同时监听主进程通知
    const unsubscribe = window.electronAPI?.onBackendReady?.(() => {
      if (!cancelled) setReady(true);
    });

    return () => {
      cancelled = true;
      if (typeof unsubscribe === 'function') unsubscribe();
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
        首次启动需要解压本地数据服务，约需 10–20 秒
      </div>
      {error && (
        <div style={{ marginTop: 16, fontSize: 12, color: 'var(--warn)', maxWidth: 320, textAlign: 'center' }}>
          {error}
        </div>
      )}
    </div>
  );
}
