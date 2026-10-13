import React from 'react';
import { expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import PrecheckProgress from './PrecheckProgress';
import type { UploadDraft } from './types';
function draft(report: UploadDraft['report'] = {}, progress = 1): UploadDraft {
  return { draft_id: 'd', group_id: 'g', status: 'preparing', prepare_progress: progress,
    report, check_digest: null, suggested_batch_no: 'sample', expires_at: '2099-01-01',
    consumed_task_id: null, consumed_task: null };
}
it('renders byte and file counts with accessible progress; hides after completion', () => {
  const view = render(<PrecheckProgress draft={draft({ prepare_stage: 'hash_input', prepare_completed: 1024 ** 3,
    prepare_total: 10 * 1024 ** 3, prepare_unit: 'bytes' }, 2)} />);
  expect(screen.getByText('校验原始文件 · 总进度 2% · 1 GB / 10 GB')).toBeTruthy();
  expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('2');
  view.rerender(<PrecheckProgress draft={draft({ prepare_stage: 'inspect_files', prepare_completed: 1200,
    prepare_total: 2000, prepare_unit: 'files' }, 73)} />);
  expect(screen.getByText('检查数据文件 · 总进度 73% · 1,200 / 2,000 个文件')).toBeTruthy();
  view.rerender(<PrecheckProgress draft={{ ...draft(), status: 'ready', prepare_progress: 100 }} />);
  expect(screen.queryByRole('progressbar')).toBeNull();
});
it('handles old server, unknown total and invalid numbers without invented counts', () => {
  const view = render(<PrecheckProgress draft={draft()} />);
  expect(screen.getByText('检查中 · 总进度 1%')).toBeTruthy();
  view.rerender(<PrecheckProgress draft={draft({ prepare_stage: 'read_table', prepare_total: null,
    prepare_completed: 0, prepare_unit: 'rows' }, 20)} />);
  expect(screen.getByText('读取表格 · 总进度 20%')).toBeTruthy();
  view.rerender(<PrecheckProgress draft={draft({ prepare_stage: 'unknown', prepare_completed: NaN,
    prepare_total: Infinity, prepare_unit: 'files' }, NaN)} />);
  expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('0');
  expect(screen.queryByText(/NaN|Infinity/)).toBeNull();
});
it('shows stop requested instead of suggesting more work is running', () => {
  render(<PrecheckProgress draft={draft({ cancel_requested: true, prepare_stage: 'hash_input',
    prepare_completed: 1, prepare_total: 100, prepare_unit: 'bytes' }, 2)} />);
  expect(screen.getByText('正在停止检查 · 总进度 2%')).toBeTruthy();
});
