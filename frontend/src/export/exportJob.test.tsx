import React from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { unverifiedManifest } from '../features/analysis/result/resultManifest';
import { createAnalysisDataset } from '../features/analysis/model/dataset';
const api = vi.hoisted(() => ({ create: vi.fn(), get: vi.fn(), cancel: vi.fn(), download: vi.fn() }));
vi.mock('../api/endpoints', () => ({ createExportJob: api.create, getExportJob: api.get, cancelExportJob: api.cancel, downloadExportArtifact: api.download }));
vi.mock('../api/capabilities', () => ({ useCapabilities: () => ({ capabilities: { mode: 'server', features: { full_export_csv: true, full_export_xlsx: true } } }) }));
import ExportDialog from './ExportDialog';
import ExportJobProgress from './ExportJobProgress';
const manifest = { ...unverifiedManifest({ filters: { eg: [1] }, fields: ['qs'], order_by: null }, 1, 200001, true), consistency: 'verified' as const, source_refs: [{ origin_id: 'bde588e1-0a9e-4bd5-887f-3cd44a7d6e75', source_batch_id: 'a72b4988-8b81-4e3a-b0b3-66f27671ac82', source_device_key: null, source_revision: 7 }] };
function dataset() { return { ...createAnalysisDataset({ id: 'edited', name: 'edited', kind: 'query', rows: [{ qs: 999 }] }), manifest, edits: [{ rowId: 'r', field: 'qs', original: 1, value: 999 }] } as any; }
beforeEach(() => { for (const fn of Object.values(api)) fn.mockReset(); api.create.mockResolvedValue({ id: 'job', status: 'pending' }); api.get.mockResolvedValue({ id: 'job', status: 'running', rows: 200001 }); api.cancel.mockResolvedValue({ id: 'job', status: 'cancel_requested' }); });
it('full query ignores plot cap and local edit, double start one captured request', async () => {
  render(<ExportDialog dataset={dataset()} onClose={vi.fn()} />);
  fireEvent.click(screen.getByLabelText('全部筛选结果（固定版本源数据）'));
  const button = screen.getByRole('button', { name: '创建完整源导出' }); fireEvent.click(button); fireEvent.click(button);
  await waitFor(() => expect(api.create).toHaveBeenCalledTimes(1));
  expect(api.create.mock.calls[0][0]).toMatchObject({ query_spec: manifest.query_spec, expected_sources: manifest.source_refs, format: 'csv' });
  expect(JSON.stringify(api.create.mock.calls[0][0])).not.toContain('999');
});
it('unverified source disables full export; workspace CSV remains available', () => {
  const data = dataset(); data.manifest = { ...manifest, consistency: 'unverified' };
  render(<ExportDialog dataset={data} onClose={vi.fn()} />);
  expect((screen.getByRole('button', { name: '下载当前工作区 CSV' }) as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(screen.getByLabelText('全部筛选结果（固定版本源数据）'));
  expect((screen.getByRole('button', { name: '创建完整源导出' }) as HTMLButtonElement).disabled).toBe(true);
  expect(api.create).not.toHaveBeenCalled();
});
it('cancel_requested has no download and remains stopping; source changed preserves analysis', async () => {
  render(<ExportDialog dataset={dataset()} onClose={vi.fn()} />); fireEvent.click(screen.getByLabelText('全部筛选结果（固定版本源数据）')); fireEvent.click(screen.getByRole('button', { name: '创建完整源导出' }));
  fireEvent.click(await screen.findByRole('button', { name: '停止导出' })); await screen.findByText(/正在停止，尚未确认/); expect(api.download).not.toHaveBeenCalled();
});
it('expired and incomplete success cannot download; verified complete success can', () => {
  const complete = { ...manifest, matched_total: 200001, returned: 200001, truncated: false };
  const view = render(<ExportJobProgress state={{ id: 'job', status: 'success', rows: 200001, manifest: complete, artifacts: [{ name: 'data.csv', download_url: '/api/export/jobs/job/artifacts/data.csv' }], expires_at: new Date(Date.now()-1000).toISOString() }} error="" cancel={vi.fn()} />);
  expect(screen.queryByRole('button', { name: '下载 data.csv' })).toBeNull();
  view.rerender(<ExportJobProgress state={{ id: 'job', status: 'success', rows: 1, manifest: complete }} error="" cancel={vi.fn()} />); expect(screen.getByText(/导出完整性证据不一致/)).toBeTruthy();
  view.rerender(<ExportJobProgress state={{ id: 'job', status: 'success', rows: 200001, manifest: complete, artifacts: [{ name: 'data.csv', download_url: '' }] }} error="" cancel={vi.fn()} />); expect(screen.getByRole('button', { name: '下载 data.csv' })).toBeTruthy();
});
it('SOURCE_CHANGED is visible and does not rebase or repeat creation', async () => {
  api.get.mockResolvedValue({ id: 'job', status: 'failed', error: { code: 'SOURCE_CHANGED', message: '来源变化' } });
  render(<ExportDialog dataset={dataset()} onClose={vi.fn()} />); fireEvent.click(screen.getByLabelText('全部筛选结果（固定版本源数据）')); fireEvent.click(screen.getByRole('button', { name: '创建完整源导出' }));
  await screen.findByText(/"SOURCE_CHANGED"/); expect(api.create).toHaveBeenCalledTimes(1); expect(api.download).not.toHaveBeenCalled();
});
