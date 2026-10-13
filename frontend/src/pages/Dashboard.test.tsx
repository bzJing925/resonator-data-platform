import React from 'react';
import { expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
const service = vi.hoisted(() => ({ getStats: vi.fn(), listTasks: vi.fn() }));
vi.mock('../api/endpoints', () => service);
vi.mock('../sync/AttentionList', () => ({ default: () => <div>异常区独立加载</div> }));
import Dashboard from './Dashboard';
it('stats failure does not hide successful tasks and missing counters remain unknown', async () => {
  service.getStats.mockRejectedValue(new Error('stats offline'));
  service.listTasks.mockResolvedValue([{ id: 31, batch_no: 'A', status: 'failed', progress_pct: 50 }]);
  render(<MemoryRouter><Dashboard /></MemoryRouter>);
  expect(await screen.findByText('31')).toBeTruthy(); expect(await screen.findByText('Error: stats offline')).toBeTruthy();
  expect(screen.getByText('异常区独立加载')).toBeTruthy(); expect(screen.queryByText('暂无任务')).toBeNull();
});
