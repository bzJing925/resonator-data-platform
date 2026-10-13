import React from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
const api = vi.hoisted(() => ({ impact: vi.fn(), remove: vi.fn(), hide: vi.fn(), remote: vi.fn(), remoteImpact: vi.fn(), trash: vi.fn(), restore: vi.fn(), caps: { features: { trash: true, remote_delete: true }, protocols: ['sync-v3'] } }));
vi.mock('../api/endpoints', () => ({ getDeletionImpact: api.impact, deleteBatch: api.remove, hideReplica: api.hide, deleteRemoteSource: api.remote, getRemoteDeletionImpact: api.remoteImpact, getTrash: api.trash, restoreBatch: api.restore }));
vi.mock('../api/capabilities', () => ({ useCapabilities: () => ({ capabilities: api.caps }) }));
import DeletionImpactDialog from './DeletionImpactDialog';
import Trash from '../pages/Trash';
const impact = { batch_no: 'lot', source_ref: { origin_id: 'origin', source_batch_id: 'batch', source_revision: 7 }, device_count: 3, scope: 'local', raw_input_available: true, active_task_id: null, purge_after: 'later' };
beforeEach(() => { for (const key of ['impact','remove','hide','remote','remoteImpact','trash','restore'] as const) api[key].mockReset(); api.caps.protocols = ['sync-v3']; api.impact.mockResolvedValue(impact); api.remoteImpact.mockResolvedValue(impact); api.trash.mockResolvedValue({ total: 1, items: [impact] }); });
it('failed preview cannot delete; user cancel invokes no mutation', async () => {
  api.impact.mockRejectedValue(new Error('预览失败')); const close = vi.fn();
  render(<DeletionImpactDialog batchNo="lot" onClose={close} onChanged={vi.fn()} />);
  await screen.findByText('预览失败'); expect((screen.getByRole('button', { name: '确认移入回收站' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '取消' })); expect(close).toHaveBeenCalledOnce(); expect(api.remove).not.toHaveBeenCalled();
});
it('active writer blocks deletion; soft delete only refreshes after success', async () => {
  api.impact.mockResolvedValue({ ...impact, active_task_id: 11 }); const changed = vi.fn();
  const view = render(<DeletionImpactDialog batchNo="lot" onClose={vi.fn()} onChanged={changed} />);
  await screen.findByText(/任务 11/); expect((screen.getByRole('button', { name: '确认移入回收站' }) as HTMLButtonElement).disabled).toBe(true); view.unmount();
  api.impact.mockResolvedValue(impact); render(<DeletionImpactDialog batchNo="lot" onClose={vi.fn()} onChanged={changed} />);
  await screen.findByText(/器件 3/); fireEvent.click(screen.getByRole('button', { name: '确认移入回收站' })); await waitFor(() => expect(changed).toHaveBeenCalledOnce()); expect(api.remove).toHaveBeenCalledWith('lot');
});
it('local hide never calls remote mutation; v2 remote deletion unavailable', async () => {
  api.impact.mockResolvedValue({ ...impact, scope: 'server' }); api.caps.protocols = ['sync-v2'];
  render(<DeletionImpactDialog batchNo="lot" onClose={vi.fn()} onChanged={vi.fn()} />);
  const remote = await screen.findByLabelText('删除服务器批次（影响所有客户端）'); expect((remote as HTMLInputElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '确认仅移除本地副本' })); await waitFor(() => expect(api.hide).toHaveBeenCalledWith(impact.source_ref, true)); expect(api.remote).not.toHaveBeenCalled(); expect(api.remove).not.toHaveBeenCalled();
});
it('remote preview version conflict preserves replica and never rebases deletion', async () => {
  api.impact.mockResolvedValue({ ...impact, scope: 'server' }); api.remoteImpact.mockResolvedValue({ ...impact, source_ref: { ...impact.source_ref, source_revision: 8 } });
  render(<DeletionImpactDialog batchNo="lot" onClose={vi.fn()} onChanged={vi.fn()} />);
  fireEvent.click(await screen.findByLabelText('删除服务器批次（影响所有客户端）')); await screen.findByText(/来源版本已变化/);
  expect((screen.getByRole('button', { name: '确认删除服务器批次' }) as HTMLButtonElement).disabled).toBe(true); expect(api.remote).not.toHaveBeenCalled();
});
it('restore 410 and name collision retain trash; successful restore reloads', async () => {
  api.restore.mockRejectedValueOnce({ response: { data: { detail: { code: 'SOURCE_UNAVAILABLE', message: '文件已清理' } } } }).mockRejectedValueOnce({ response: { data: { detail: { code: 'RESTORE_NAME_CONFLICT', message: '名称已占用' } } } }).mockResolvedValue({});
  render(<Trash />); const button = await screen.findByRole('button', { name: '恢复 lot' });
  fireEvent.click(button); await screen.findByText(/文件已清理/); expect(screen.getByRole('button', { name: '恢复 lot' })).toBeTruthy();
  fireEvent.click(button); await screen.findByText(/名称已占用/); expect(api.trash).toHaveBeenCalledTimes(1);
  fireEvent.click(button); await waitFor(() => expect(api.trash).toHaveBeenCalledTimes(2));
});
