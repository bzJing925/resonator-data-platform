import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import BootGate from './BootGate';
import { getHealth } from '../api/endpoints';
import type { ElectronAPI } from '../types';

vi.mock('../api/endpoints', () => ({
  getHealth: vi.fn().mockRejectedValue(new Error('connection refused')),
}));

const mockedGetHealth = vi.mocked(getHealth);

function installElectronAPI(overrides: Partial<ElectronAPI> = {}) {
  const api: ElectronAPI = {
    getVersion: vi.fn().mockResolvedValue('0.0.0-test'),
    getBackendUrl: vi.fn().mockResolvedValue('http://127.0.0.1:59999'),
    isBackendReady: vi.fn().mockResolvedValue(false),
    onBackendReady: vi.fn(),
    onBackendStateChange: vi.fn(),
    openExternal: vi.fn(),
    platform: 'darwin',
    ...overrides,
  };
  window.electronAPI = api;
  return api;
}

afterEach(() => {
  delete window.electronAPI;
  mockedGetHealth.mockClear();
});

describe('BootGate', () => {
  it('非 Electron 环境直接渲染子内容', () => {
    render(
      <BootGate>
        <div>应用内容</div>
      </BootGate>
    );
    expect(screen.getByText('应用内容')).toBeTruthy();
  });

  it('Electron 且主进程标记后端已就绪：直接渲染，不发任何 HTTP 健康请求', async () => {
    installElectronAPI({ isBackendReady: vi.fn().mockResolvedValue(true) });
    render(
      <BootGate>
        <div>应用内容</div>
      </BootGate>
    );
    expect(await screen.findByText('应用内容')).toBeTruthy();
    // 等待超过旧实现的 200ms 轮询周期，确保没有任何轮询被触发
    await new Promise((r) => setTimeout(r, 600));
    expect(mockedGetHealth).not.toHaveBeenCalled();
  });

  it('Electron 未就绪：显示启动页，靠 IPC 轮询等待，全程零 HTTP 请求', async () => {
    const isBackendReady = vi
      .fn()
      .mockResolvedValueOnce(false)
      .mockResolvedValueOnce(false)
      .mockResolvedValue(true);
    installElectronAPI({ isBackendReady });
    render(
      <BootGate>
        <div>应用内容</div>
      </BootGate>
    );
    // 未就绪时展示启动页
    expect(screen.getByText(/正在启动本地数据服务/)).toBeTruthy();
    // IPC 轮询发现就绪后放行（轮询间隔 500ms，给足余量）
    expect(await screen.findByText('应用内容', undefined, { timeout: 4000 })).toBeTruthy();
    // 关键断言：启动等待期间不允许产生任何 HTTP /api/health 请求
    // （后端未起时每个失败请求都会在控制台刷一条 ERR_CONNECTION_REFUSED）
    expect(mockedGetHealth).not.toHaveBeenCalled();
  });

  it('Electron 未就绪：收到主进程 ready 状态通知后放行，且零 HTTP 请求', async () => {
    let stateCb: ((state: 'starting' | 'ready' | 'error') => void) | undefined;
    installElectronAPI({
      onBackendStateChange: vi.fn((cb) => {
        stateCb = cb;
      }),
    });
    render(
      <BootGate>
        <div>应用内容</div>
      </BootGate>
    );
    expect(screen.getByText(/正在启动本地数据服务/)).toBeTruthy();
    stateCb?.('ready');
    expect(await screen.findByText('应用内容')).toBeTruthy();
    await new Promise((r) => setTimeout(r, 600));
    expect(mockedGetHealth).not.toHaveBeenCalled();
  });
});
