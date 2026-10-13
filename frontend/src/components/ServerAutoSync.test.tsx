import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { getServerSyncStatus, pullFromServer } from '../api/endpoints';

vi.mock('../api/capabilities', () => ({ getCapabilities: vi.fn().mockResolvedValue({ mode: 'desktop', features: { replication_v3: true } }) }));

vi.mock('../api/endpoints', () => ({
  getServerSyncStatus: vi.fn(),
  pullFromServer: vi.fn(),
}));

const mockedStatus = vi.mocked(getServerSyncStatus);
const mockedPull = vi.mocked(pullFromServer);

const baseStatus = {
  enabled: true,
  configured: true,
  server_url: 'http://server:8000',
  username: 'u',
  reachable: true,
  unsynced_batches: [],
};

// 模块级 attempted 标记需逐用例重置：resetModules + 动态 import
async function renderFresh() {
  vi.resetModules();
  const { default: ServerAutoSync } = await import('./ServerAutoSync');
  return render(<ServerAutoSync />);
}

afterEach(() => {
  mockedStatus.mockReset();
  mockedPull.mockReset();
});

describe('ServerAutoSync', () => {
  it('非桌面模式不探测不同步', async () => {
    mockedStatus.mockResolvedValue({ ...baseStatus, enabled: false });
    renderFresh();
    await waitFor(() => expect(mockedStatus).toHaveBeenCalledTimes(1));
    expect(mockedPull).not.toHaveBeenCalled();
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('服务器不可达时静默（不同步、无提示）', async () => {
    mockedStatus.mockResolvedValue({ ...baseStatus, reachable: false });
    renderFresh();
    await waitFor(() => expect(mockedStatus).toHaveBeenCalledTimes(1));
    expect(mockedPull).not.toHaveBeenCalled();
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('可达且有新数据：自动拉取并提示新增数量', async () => {
    mockedStatus.mockResolvedValue(baseStatus);
    mockedPull.mockResolvedValue({ mappings_added: 0, batches_added: 2, devices_added: 30 });
    renderFresh();
    await screen.findByText('同步完成：新增 2、更新 0、删除 0、表格回传 0、原始批次回传 0、对照表上传 0、对照表收到 0');
    expect(mockedPull).toHaveBeenCalledTimes(1);
  });

  it('可达但无新数据：提示已是最新', async () => {
    mockedStatus.mockResolvedValue(baseStatus);
    mockedPull.mockResolvedValue({ mappings_added: 0, batches_added: 0, devices_added: 0 });
    renderFresh();
    await screen.findByText('服务器已连接，数据已是最新');
  });

  it('只收到独立对照表时也提示并刷新数据', async () => {
    mockedStatus.mockResolvedValue(baseStatus);
    mockedPull.mockResolvedValue({ mappings_added: 1, batches_added: 0, devices_added: 0 });
    const listener = vi.fn();
    window.addEventListener('aln-data-synced', listener);
    try {
      renderFresh();
      await screen.findByText('同步完成：新增 0、更新 0、删除 0、表格回传 0、原始批次回传 0、对照表上传 0、对照表收到 1');
      expect(listener).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener('aln-data-synced', listener);
    }
  });

  it('拉取失败静默（不打断应用）', async () => {
    mockedStatus.mockResolvedValue(baseStatus);
    mockedPull.mockRejectedValue(new Error('502'));
    renderFresh();
    await waitFor(() => expect(mockedPull).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('status')).toBeNull();
  });
});

it('先离线后联网会自动重试，无需重启软件', async () => {
  mockedStatus.mockResolvedValueOnce({ ...baseStatus, reachable: false })
    .mockResolvedValue(baseStatus);
  mockedPull.mockResolvedValue({ mappings_added: 0, batches_added: 0, devices_added: 0 });
  await renderFresh();
  await waitFor(() => expect(mockedStatus).toHaveBeenCalledTimes(1));
  window.dispatchEvent(new Event('online'));
  await waitFor(() => expect(mockedPull).toHaveBeenCalledTimes(1));
});

it('本地上传完成后立即尝试同步', async () => {
  mockedStatus.mockResolvedValue(baseStatus);
  mockedPull.mockResolvedValue({ mappings_added: 0, batches_added: 0, devices_added: 0 });
  await renderFresh();
  await waitFor(() => expect(mockedPull).toHaveBeenCalledTimes(1));
  window.dispatchEvent(new Event('aln-local-data-changed'));
  await waitFor(() => expect(mockedPull).toHaveBeenCalledTimes(2));
});
