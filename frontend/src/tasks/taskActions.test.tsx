import React from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, renderHook, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { TaskEventGate } from './taskStatus';
const api = vi.hoisted(() => ({ cancel: vi.fn(), task: vi.fn(), options: vi.fn(), retry: vi.fn() }));
vi.mock('../api/endpoints', () => ({ cancelTask: api.cancel, getTask: api.task, getRetryOptions: api.options, retryTask: api.retry }));
vi.mock('../api/capabilities', () => ({ useCapabilities: () => ({ capabilities: { features: { processing_reports: true } } }) }));
import TaskActions from './TaskActions';
import useSSE from '../hooks/useSSE';
const item = { id: 'failed-one', unit_kind: 'device', source_member: 'dut/a.s2p', port: 'S11', status: 'failed', error_code: 'BAD_DATA' };
beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  api.options.mockResolvedValue({ task_id: 1, run_id: 'run', expected_source_revision: 7, retry_of_task_id: 9, items: [item], next_offset: null });
  api.task.mockResolvedValue({ id: 1, run_id: 'run', status: 'cancel_requested', error_msg: 'STOP_NOT_CONFIRMED' });
  api.cancel.mockResolvedValue({ id: 1, run_id: 'run', status: 'cancel_requested' });
  api.retry.mockResolvedValue({ task_id: '2', run_id: 'new', status: 'pending' });
});
it('stop request remains visible until GET confirms cancelled, preserving data explanation', async () => {
  function Harness() { const [task, setTask] = React.useState({ id: 1, run_id: 'run', status: 'running' }); return <TaskActions task={task} onUpdate={(next) => setTask(next as typeof task)} />; }
  render(<MemoryRouter><Harness /></MemoryRouter>);
  fireEvent.click(screen.getByRole('button', { name: '停止处理' }));
  expect(await screen.findByText(/执行器尚未确认/)).toBeTruthy();
  expect((screen.getByRole('button', { name: '停止处理' }) as HTMLButtonElement).disabled).toBe(true);
  expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('保留输入及已发布结果'));
  expect(api.cancel).toHaveBeenCalledTimes(1);
});
it('double retry uses captured revision and one request, old report and parent remain linked', async () => {
  render(<MemoryRouter><TaskActions task={{ id: 1, run_id: 'run', status: 'failed' }} /></MemoryRouter>);
  fireEvent.click(await screen.findByRole('checkbox'));
  const button = screen.getByRole('button', { name: '重试所选失败项' }); fireEvent.click(button); fireEvent.click(button);
  expect(await screen.findByText(/查看新重试任务 2/)).toBeTruthy(); expect(api.retry).toHaveBeenCalledTimes(1);
  expect(api.retry.mock.calls[0][1]).toMatchObject({ expected_source_revision: 7, item_ids: ['failed-one'], mode: 'failed' });
  expect(screen.getByRole('link', { name: '任务 9' }).getAttribute('href')).toBe('/tasks/9');
});
it('409 preserves selection and original expected revision; does not refresh or overwrite', async () => {
  api.retry.mockRejectedValue({ response: { status: 409, data: { detail: { code: 'RETRY_INPUT_CHANGED', message: '版本变化' } } } });
  render(<MemoryRouter><TaskActions task={{ id: 1, run_id: 'run', status: 'failed' }} /></MemoryRouter>);
  const checkbox = await screen.findByRole('checkbox'); fireEvent.click(checkbox); fireEvent.click(screen.getByRole('button', { name: '重试所选失败项' }));
  await screen.findByText(/输入或版本冲突/); expect((checkbox as HTMLInputElement).checked).toBe(true);
  expect(api.options).toHaveBeenCalledTimes(1); expect(api.retry.mock.calls[0][1].expected_source_revision).toBe(7);
  expect((screen.getByRole('button', { name: '重试所选失败项' }) as HTMLButtonElement).disabled).toBe(true);
});
it('run and sequence gate ignores old run, duplicate, late done after stopping and terminal regressions', () => {
  const gate = new TaskEventGate(); expect(gate.accept({ run_id: 'current', status: 'running' }, true)).toBe(true);
  expect(gate.accept({ schema_version: 1, run_id: 'old', event_seq: 100, status: 'success' })).toBe(false);
  expect(gate.accept({ schema_version: 1, run_id: 'current', event_seq: 3, status: 'cancel_requested' })).toBe(true);
  expect(gate.accept({ schema_version: 1, run_id: 'current', event_seq: 2, status: 'running' })).toBe(false);
  expect(gate.accept({ schema_version: 1, run_id: 'current', event_seq: 4, status: 'success' })).toBe(false);
  expect(gate.accept({ run_id: 'current', status: 'cancelled' }, true)).toBe(true);
  expect(gate.accept({ run_id: 'current', status: 'running' }, true)).toBe(false);
});
it('event transport failure leaves running and GET fallback confirms cancellation; unmount stops polling', async () => {
  class Stream { static instance: Stream; listeners: Record<string, (e: Event) => void> = {}; onerror?: () => void; close = vi.fn(); constructor() { Stream.instance = this; } addEventListener(type: string, fn: (e: Event) => void) { this.listeners[type] = fn; } }
  vi.stubGlobal('EventSource', Stream);
  api.task.mockResolvedValueOnce({ id: 1, run_id: 'run', status: 'running' }).mockResolvedValue({ id: 1, run_id: 'run', status: 'cancelled' });
  const hook = renderHook(() => useSSE(1));
  await waitFor(() => expect(Stream.instance).toBeTruthy());
  act(() => Stream.instance.onerror?.());
  expect(hook.result.current.status).toBe('running'); expect(hook.result.current.done).toBe(false);
  await waitFor(() => expect(hook.result.current.status).toBe('cancelled'), { timeout: 2200 });
  expect(hook.result.current.done).toBe(true); hook.unmount();
  expect(Stream.instance.close).toHaveBeenCalled(); vi.unstubAllGlobals();
});

it('confirmed cancelled is the only stopped label; retry GET failure leaves unknown', async () => {
  api.options.mockRejectedValue(new Error('离线'));
  render(<MemoryRouter><TaskActions task={{ id: 1, run_id: 'run', status: 'cancelled' }} /></MemoryRouter>);
  expect(await screen.findByText('已停止；输入与已发布结果保留。')).toBeTruthy();
  await screen.findByText('离线');
  expect((screen.getByRole('button', { name: '重试所选失败项' }) as HTMLButtonElement).disabled).toBe(true);
});
