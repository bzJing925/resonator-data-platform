import React from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { UploadProgressProvider, useUploadProgress } from '../contexts/UploadProgressContext';
import FloatingUploadProgress from '../components/FloatingUploadProgress';
import type { UploadDraft, UploadOptions } from './types';
const service = vi.hoisted(() => ({ draft: vi.fn(), prepare: vi.fn(), group: vi.fn(), commit: vi.fn(), recheck: vi.fn(), remove: vi.fn(), task: vi.fn(), report: vi.fn(), download: vi.fn() }));
vi.mock('../api/endpoints', () => ({ getUploadDraft: service.draft, prepareUploadDraft: service.prepare, getUploadGroup: service.group, commitUploadDraft: service.commit, recheckUploadDraft: service.recheck, deleteUploadDraft: service.remove, getTask: service.task, getProcessingReport: service.report, downloadProcessingIssues: service.download }));
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

it('expired draft is deleted and uploaded again; fatal stays blocked', async () => {
  let expired = true;
  service.prepare.mockImplementation((body: FormData) => { groupId = String(body.get('group_id')); drafts = [{ ...ready(), ...(expired ? { status: 'expired' as const, expires_at: new Date(Date.now()-1000).toISOString() } : {}) }]; return Promise.resolve({ draft_id: 'draft-a' }); });
  service.remove.mockImplementation(() => { expired = false; drafts = []; return Promise.resolve({ draft_id: 'draft-a', status: 'deleted', stop_confirmed: true }); });
  mount(); fireEvent.click(screen.getByRole('button', { name: '检查数据' }));
  await screen.findByText(/草稿已过期，请删除后重新上传/);
  expect(screen.queryByRole('button', { name: '启动此文件入库' })).toBeNull();
  expect(screen.queryByRole('button', { name: '复查此文件' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '删除过期草稿' }));
  await waitFor(() => expect(service.remove).toHaveBeenCalledWith('draft-a'));
  await waitFor(() => expect(screen.queryByTestId('upload-entry-a.zip')).toBeNull());
  fireEvent.click(screen.getByRole('button', { name: '检查数据' }));
  await screen.findByText(/检查结果/);
  expect(service.prepare).toHaveBeenCalledTimes(2);
  expect(service.recheck).not.toHaveBeenCalled();
  drafts = [{ ...drafts[0], report: { ...drafts[0].report, fatal_count: 1 } }];
  fireEvent.click(screen.getByRole('button', { name: '刷新组状态' }));
  await screen.findByText('存在致命问题，禁止启动入库。');
  fireEvent.click(screen.getByLabelText('已审查警告 IGNORED_FILE'));
  expect((screen.getByRole('button', { name: '启动此文件入库' }) as HTMLButtonElement).disabled).toBe(true);
  expect(service.commit).not.toHaveBeenCalled();
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

function staleEntry(file: File) {
  const id = 'old-group';
  window.history.replaceState({}, '', '/upload?groupId=' + id);
  localStorage.setItem('aln_upload_group:' + id, JSON.stringify([{ key: JSON.stringify([file.name, file.size, file.lastModified]), name: file.name, size: file.size, modified: file.lastModified, requestKey: 'original-request', draftId: 'missing-draft', optionsKey: JSON.stringify(options) }]));
}
it('reselecting a cached missing draft uploads again with its original idempotency key', async () => {
  const file = new File(['raw'], '#3.zip', { lastModified: 1 }); staleEntry(file);
  service.draft.mockRejectedValue({ response: { status: 404 } });
  mount([file]); fireEvent.click(screen.getByRole('button', { name: '检查数据' }));
  await waitFor(() => expect(service.prepare).toHaveBeenCalledTimes(1));
  expect(service.prepare.mock.calls[0][0].get('request_key')).toBe('original-request');
  await screen.findByText(/器件\/端口 2/);
  expect(JSON.parse(localStorage.getItem('aln_upload_group:old-group')!)[0].draftId).toBe('draft-a');
});
it('an unreachable cached draft is not treated as missing or reuploaded', async () => {
  const file = new File(['raw'], '#3.zip', { lastModified: 1 }); staleEntry(file);
  service.draft.mockRejectedValue(new Error('服务器不可达'));
  mount([file]); fireEvent.click(screen.getByRole('button', { name: '检查数据' }));
  await screen.findByText('服务器不可达'); expect(service.prepare).not.toHaveBeenCalled();
  expect(JSON.parse(localStorage.getItem('aln_upload_group:old-group')!)[0].draftId).toBe('missing-draft');
});
it('an existing draft is recovered with guidance instead of silently skipping or uploading twice', async () => {
  const file = new File(['raw'], '#3.zip', { lastModified: 1 }); staleEntry(file);
  service.draft.mockImplementation(() => Promise.resolve(ready('missing-draft')));
  mount([file]); fireEvent.click(screen.getByRole('button', { name: '检查数据' }));
  await screen.findByText(/此文件已有检查记录/);
  expect(service.prepare).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: '复查此文件' })).toBeTruthy();
});

it('successful tasks stay out of restored toasts and stop terminal polling', async () => {
  drafts = [{ ...ready('#3'), status: 'consumed', consumed_task_id: 3,
    consumed_task: { task_id: '3', batch_no: '#3', status: 'success', stage: 'done', progress_pct: 100 } }];
  service.task.mockResolvedValue({ id: 3, status: 'success' });
  render(<MemoryRouter><UploadProgressProvider><UploadGroup files={[]} options={options} /><FloatingUploadProgress /></UploadProgressProvider></MemoryRouter>);
  expect(await screen.findByTestId('upload-entry-#3')).toBeTruthy();
  await waitFor(() => expect(service.report).toHaveBeenCalledTimes(1));
  expect(screen.queryByText(/上传任务 \(/)).toBeNull();
  const groupCalls = service.group.mock.calls.length;
  await new Promise((resolve) => setTimeout(resolve, 1200));
  expect(service.group).toHaveBeenCalledTimes(groupCalls);
  expect(service.task).toHaveBeenCalledTimes(1);
  expect(service.report).toHaveBeenCalledTimes(1);
});

function CompletedToast() {
  const { addTask } = useUploadProgress();
  return <><button onClick={() => addTask({ task_id: '3', batch_no: '#3', status: 'success', done: true })}>显示完成通知</button><FloatingUploadProgress /></>;
}
it('completed toast expires after 30 seconds without another render', () => {
  vi.useFakeTimers();
  try {
    render(<MemoryRouter><UploadProgressProvider><CompletedToast /></UploadProgressProvider></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: '显示完成通知' }));
    expect(screen.getByText('上传任务 (1)')).toBeTruthy();
    act(() => vi.advanceTimersByTime(30001));
    expect(screen.queryByText(/上传任务 \(/)).toBeNull();
  } finally { vi.useRealTimers(); }
});

it('failed and cancelled uploads stay hidden while a ready draft is restored', async () => {
  const stopped = { ...ready('stopped'), status: 'consumed' as const, consumed_task_id: 22,
    consumed_task: { task_id: '22', batch_no: 'stopped', status: 'cancelled', stage: 'failed', progress_pct: 30 } };
  const failed = { ...ready('failed'), status: 'consumed' as const, consumed_task_id: 23,
    consumed_task: { task_id: '23', batch_no: 'failed', status: 'failed', stage: 'failed', progress_pct: 60 } };
  drafts = [stopped, failed, ready('paused')];
  mount([]);
  expect(await screen.findByTestId('upload-entry-paused')).toBeTruthy();
  expect(screen.queryByTestId('upload-entry-stopped')).toBeNull();
  expect(screen.queryByTestId('upload-entry-failed')).toBeNull();
  expect(service.task).not.toHaveBeenCalledWith(22);
  expect(service.task).not.toHaveBeenCalledWith(23);
  expect(screen.queryByText(/上传任务 \(/)).toBeNull();
});
