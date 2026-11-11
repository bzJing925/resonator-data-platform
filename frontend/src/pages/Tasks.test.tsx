import React from 'react';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { listTasks } from '../api/endpoints';
import Tasks from './Tasks';

vi.mock('../api/endpoints', () => ({ listTasks: vi.fn() }));

const task = (id: number, batch_no: string, status: string) => ({
  id, batch_no, status, progress_pct: 0, stage: 'extract', stage_progress_pct: 0,
  started_at: '2026-10-26T08:00:00', finished_at: null,
});

beforeEach(() => {
  vi.mocked(listTasks).mockReset();
  vi.mocked(listTasks).mockResolvedValue([
    task(1, 'keep-success', 'success'),
    task(2, 'hide-failed', 'failed'),
    task(3, 'hide-cancelled', 'cancelled'),
    task(4, 'keep-running', 'running'),
    task(5, 'keep-stopping', 'cancel_requested'),
  ] as any);
});

it('hides failed and cancelled uploads but keeps active and successful tasks', async () => {
  render(<MemoryRouter><Tasks /></MemoryRouter>);
  expect(await screen.findByText('keep-success')).toBeTruthy();
  expect(screen.getByText('keep-running')).toBeTruthy();
  expect(screen.getByText('keep-stopping')).toBeTruthy();
  expect(screen.queryByText('hide-failed')).toBeNull();
  expect(screen.queryByText('hide-cancelled')).toBeNull();
});
