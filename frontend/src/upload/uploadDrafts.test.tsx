import React from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { UploadProgressProvider } from '../contexts/UploadProgressContext';
import type { UploadDraft, UploadOptions } from './types';
const service = vi.hoisted(() => ({ prepare: vi.fn(), group: vi.fn(), commit: vi.fn(), recheck: vi.fn(), task: vi.fn(), report: vi.fn(), download: vi.fn() }));
vi.mock('../api/endpoints', () => ({ prepareUploadDraft: service.prepare, getUploadGroup: service.group, commitUploadDraft: service.commit, recheckUploadDraft: service.recheck, getTask: service.task, getProcessingReport: service.report, downloadProcessingIssues: service.download }));
vi.mock('../api/capabilities', () => ({ useCapabilities: () => ({ capabilities: { features: { upload_precheck: true } } }) }));
import UploadGroup from './UploadGroup';
const options: UploadOptions = { mapping_id: 1, f_start_ghz: 4, f_end_ghz: 5, process_type: 'AUTO', deembed: false, deembed_method: 'default', target_batch: '' };
let drafts: UploadDraft[] = [];
let groupId = '';
function ready(id = 'draft-a'): UploadDraft { return { draft_id: id, group_id: groupId, status: 'ready', prepare_progress: 100, check_digest: 'digest-a', report: { quality_status: 'partial', fatal_count: 0, warning_codes: ['IGNORED_FILE'], issues: [{ code: 'IGNORED_FILE', severity: 'warning', message: '忽略说明文件' }], items: [{ classification: 'dut', status: 'success', ports: ['S11','S22'] }] }, suggested_batch_no: id, expires_at: new Date(Date.now()+60000).toISOString(), consumed_task_id: null, consumed_task: null }; }
function mount(files = [new File(['raw'], 'a.zip', { lastModified: 1 })]) { return render(<MemoryRouter><UploadProgressProvider><UploadGroup files={files} options={options} /></UploadProgressProvider></MemoryRouter>); }
beforeEach(() => {
  window.localStorage.clear(); window.history.replaceState({}, '', '/'); drafts = []; groupId = '';
  for (const mock of Object.values(service)) mock.mockReset();
  service.group.mockImplementation((id) => { groupId = id; return Promise.resolve({ group_id: id, drafts }); });
  service.prepare.mockImplementation((body: FormData) => { groupId = String(body.get('group_id')); drafts = [ready()]; return Promise.resolve({ draft_id: 'draft-a' }); });
  service.commit.mockImplementation(() => { drafts[0] = { ...drafts[0], status: 'consumed', consumed_task_id: 11, consumed_task: { task_id: '11', batch_no: 'a', status: 'success', stage: 'done', progress_pct: 100 } }; return Promise.resolve({ task_id: '11', run_id: 'run', batch_no: 'a', status: 'pending' }); });
  service.task.mockResolvedValue({ id: 11, status: 'success' });
  service.report.mockResolvedValue({ run_id: 'run', quality_status: 'partial', summary: { file_counts: { total: 2, skipped: 1 }, device_counts: { total: 2, success: 1, failed: 1 }, published_count: 1 }, issues: [], next_offset: null });
});
it('S2P two ports, explicit warning gate, double commit once and partial acceptance retained without reupload', async () => {
  mount(); fireEvent.click(screen.getByRole('button', { name: '检查数据' }));
  expect(await screen.findByText(/器件\/端口 2/)).toBeTruthy();
  const commit = screen.getByRole('button', { name: '启动此文件入库' }); expect((commit as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByLabelText('已审查警告 IGNORED_FILE')); fireEvent.click(commit); fireEvent.click(commit);
  await waitFor(() => expect(service.commit).toHaveBeenCalledTimes(1));
  expect(await screen.findByText(/部分成功/)).toBeTruthy(); expect(service.prepare).toHaveBeenCalledTimes(1);
  expect(service.commit.mock.calls[0][1].acknowledged_warning_codes).toEqual(['IGNORED_FILE']);
});
it('refresh restores server draft and original request key while no File is retained', async () => {
  const first = mount(); fireEvent.click(screen.getByRole('button', { name: '检查数据' })); await screen.findByText(/器件\/端口 2/); first.unmount();
  mount([]); expect(await screen.findByText(/器件\/端口 2/)).toBeTruthy(); fireEvent.click(screen.getByLabelText('已审查警告 IGNORED_FILE')); fireEvent.click(screen.getByRole('button', { name: '启动此文件入库' }));
  await waitFor(() => expect(service.commit).toHaveBeenCalledTimes(1)); expect(service.prepare).toHaveBeenCalledTimes(1);
});
it('stale evidence triggers recheck and never resubmits file or automatically commits new report', async () => {
  service.commit.mockRejectedValue({ response: { data: { detail: { code: 'PRECHECK_STALE', message: '旧报告' } } } });
  service.recheck.mockImplementation(() => { drafts[0] = { ...drafts[0], check_digest: 'new-digest' }; return Promise.resolve({}); });
  mount(); fireEvent.click(screen.getByRole('button', { name: '检查数据' })); await screen.findByText(/器件\/端口 2/); fireEvent.click(screen.getByLabelText('已审查警告 IGNORED_FILE')); fireEvent.click(screen.getByRole('button', { name: '启动此文件入库' }));
  await waitFor(() => expect(service.recheck).toHaveBeenCalledTimes(1)); expect(await screen.findByText(/检查证据已变化/)).toBeTruthy(); expect(service.prepare).toHaveBeenCalledTimes(1); expect(service.commit).toHaveBeenCalledTimes(1);
});
it('zero bytes and duplicate File metadata reject before prepare', async () => {
  let view = mount([new File([], 'empty.zip')]); fireEvent.click(screen.getByRole('button', { name: '检查数据' })); expect(await screen.findByText('零字节文件不能检查')).toBeTruthy(); view.unmount();
  const file = new File(['x'], 'a.zip', { lastModified: 1 }); view = mount([file, file]); fireEvent.click(screen.getByRole('button', { name: '检查数据' })); expect(await screen.findByText(/选择了相同名称/)).toBeTruthy(); expect(service.prepare).not.toHaveBeenCalled(); view.unmount();
});

it('expired draft cannot commit; explicit recheck renews evidence and warning review; fatal stays blocked', async () => {
  service.prepare.mockImplementation((body: FormData) => { groupId = String(body.get('group_id')); drafts = [{ ...ready(), status: 'expired', expires_at: new Date(Date.now()-1000).toISOString() }]; return Promise.resolve({ draft_id: 'draft-a' }); });
  service.recheck.mockImplementation(() => { drafts = [{ ...ready(), check_digest: 'renewed' }]; return Promise.resolve({}); });
  mount(); fireEvent.click(screen.getByRole('button', { name: '检查数据' }));
  await screen.findByText(/草稿已过期/);
  fireEvent.click(screen.getByLabelText('已审查警告 IGNORED_FILE'));
  expect((screen.getByRole('button', { name: '启动此文件入库' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '复查此文件' }));
  await waitFor(() => expect(service.recheck).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(screen.queryByText(/草稿已过期/)).toBeNull());
  expect((screen.getByLabelText('已审查警告 IGNORED_FILE') as HTMLInputElement).checked).toBe(false);
  drafts = [{ ...drafts[0], report: { ...drafts[0].report, fatal_count: 1 } }];
  fireEvent.click(screen.getByRole('button', { name: '刷新组状态' }));
  await screen.findByText('存在致命问题，禁止启动入库。');
  fireEvent.click(screen.getByLabelText('已审查警告 IGNORED_FILE'));
  expect((screen.getByRole('button', { name: '启动此文件入库' }) as HTMLButtonElement).disabled).toBe(true);
  expect(service.commit).not.toHaveBeenCalled(); expect(service.prepare).toHaveBeenCalledTimes(1);
});

it.each([[null, null], [4, null], [null, 5]])('empty frequency bounds %s/%s remain automatic through prepare, recheck and commit', async (start, end) => {
  const automatic = { ...options, f_start_ghz: start, f_end_ghz: end };
  render(<MemoryRouter><UploadProgressProvider><UploadGroup files={[new File(['raw'], 'auto.zip')]} options={automatic} /></UploadProgressProvider></MemoryRouter>);
  fireEvent.click(screen.getByRole('button', { name: '检查数据' }));
  await screen.findByText(/器件\/端口 2/);
  const body = service.prepare.mock.calls[0][0] as FormData;
  expect(body.get('f_start_ghz')).toBe(start === null ? null : String(start));
  expect(body.get('f_end_ghz')).toBe(end === null ? null : String(end));
  fireEvent.click(screen.getByRole('button', { name: '复查此文件' }));
  await waitFor(() => expect(service.recheck).toHaveBeenCalledWith('draft-a', automatic));
  await waitFor(() => expect((screen.getByRole('button', { name: '复查此文件' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByLabelText('已审查警告 IGNORED_FILE'));
  fireEvent.click(screen.getByRole('button', { name: '启动此文件入库' }));
  await waitFor(() => expect(service.commit).toHaveBeenCalledTimes(1));
  expect(service.prepare).toHaveBeenCalledTimes(1);
});

it.each([[NaN, null], [null, Infinity], [0, null], [-1, 5], [5, 4], [4, 4]])('invalid explicit frequency %s/%s does not upload', async (start, end) => {
  render(<MemoryRouter><UploadProgressProvider><UploadGroup files={[new File(['raw'], 'bad.zip')]} options={{ ...options, f_start_ghz: start, f_end_ghz: end }} /></UploadProgressProvider></MemoryRouter>);
  fireEvent.click(screen.getByRole('button', { name: '检查数据' }));
  await screen.findByText(/频率须为有限正数/);
  expect(service.prepare).not.toHaveBeenCalled();
});

it('polls stage fields, disables recheck during processing, and shows only completed reports', async () => {
  service.prepare.mockImplementation((body: FormData) => {
    groupId = String(body.get('group_id'));
    drafts = [{ ...ready(), status: 'preparing', prepare_progress: 8, check_digest: null,
      report: { prepare_stage: 'hash_input', prepare_completed: 3 * 1024 ** 3,
        prepare_total: 10 * 1024 ** 3, prepare_unit: 'bytes' } }];
    return Promise.resolve({ draft_id: 'draft-a' });
  });
  mount(); fireEvent.click(screen.getByRole('button', { name: '检查数据' }));
  expect(await screen.findByText('校验原始文件 · 总进度 8% · 3 GB / 10 GB')).toBeTruthy();
  expect((screen.getByRole('button', { name: '复查此文件' }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryByText(/检查结果/)).toBeNull();
  drafts = [{ ...drafts[0], prepare_progress: 70, report: { prepare_stage: 'inspect_files',
    prepare_completed: 1000, prepare_total: 2000, prepare_unit: 'files' } }];
  expect(await screen.findByText('检查数据文件 · 总进度 70% · 1,000 / 2,000 个文件', {}, { timeout: 2500 })).toBeTruthy();
  drafts = [ready()];
  expect(await screen.findByText(/检查完成/, {}, { timeout: 2500 })).toBeTruthy();
  expect(screen.queryByRole('progressbar')).toBeNull();
  expect(screen.getByText(/器件\/端口 2/)).toBeTruthy();
  expect(service.prepare).toHaveBeenCalledTimes(1);
});
