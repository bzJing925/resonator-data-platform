import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import PrecheckReport from './PrecheckReport';
import type { UploadDraft } from './types';

function draft(): UploadDraft {
  return {
    draft_id: 'draft-a', group_id: 'group-a', status: 'ready', prepare_progress: 100,
    check_digest: 'digest-a',
    report: {
      quality_status: 'partial', fatal_count: 0,
      warning_codes: ['MAPPING_NOT_FOUND', 'UNRELATED_FILE'],
      issues: [
        { code: 'MAPPING_NOT_FOUND', severity: 'info', message: '器件未命中对照表：入库但不参与分析', relpath: 'D1/one.s1p' },
        { code: 'MAPPING_NOT_FOUND', severity: 'info', message: '器件未命中对照表：入库但不参与分析', relpath: 'D2/two.s1p' },
        { code: 'UNRELATED_FILE', severity: 'warning', message: '无关文件将排除', relpath: 'notes.txt' },
      ],
      items: [
        { relpath: 'D1/one.s1p', classification: 'dut', status: 'success', ports: ['S11'] },
        { relpath: 'D2/two.s1p', classification: 'dut', status: 'success', ports: ['S11'] },
      ],
    },
    suggested_batch_no: 'batch-a', expires_at: new Date(Date.now() + 60000).toISOString(),
    consumed_task_id: null, consumed_task: null,
  };
}

it('keeps all issues collapsed and groups device names inline by issue type', () => {
  const { container } = render(<PrecheckReport draft={draft()} disabled={false} onCommit={vi.fn()} />);
  const details = container.querySelector('.precheck-issues') as HTMLDetailsElement;
  expect(details.open).toBe(false);
  fireEvent.click(screen.getByText('检查问题（3 项）'));
  expect(details.open).toBe(true);
  const mapping = screen.getByText('器件未命中对照表').closest('p')!;
  expect(mapping.textContent).toBe('器件未命中对照表：one.s1p、two.s1p');
  expect(mapping.querySelectorAll('li, br')).toHaveLength(0);
  expect(screen.getByText('无关文件将排除').closest('p')!.textContent).toBe('无关文件将排除：notes.txt');
});

it('does not require mapping acknowledgement but still gates other warnings', () => {
  const onCommit = vi.fn();
  render(<PrecheckReport draft={draft()} disabled={false} onCommit={onCommit} />);
  expect(screen.queryByLabelText('已审查警告 MAPPING_NOT_FOUND')).toBeNull();
  const commit = screen.getByRole('button', { name: '启动此文件入库' }) as HTMLButtonElement;
  expect(commit.disabled).toBe(true);
  fireEvent.click(screen.getByLabelText('已审查警告 UNRELATED_FILE'));
  expect(commit.disabled).toBe(false);
  fireEvent.click(commit);
  expect(onCommit).toHaveBeenCalledWith(['UNRELATED_FILE', 'MAPPING_NOT_FOUND']);
});

it('allows immediate commit when mapping miss is the only issue', () => {
  const value = draft();
  value.report.warning_codes = [];
  value.report.issues = value.report.issues!.filter((issue) => issue.code === 'MAPPING_NOT_FOUND');
  value.report.quality_status = 'complete';
  const onCommit = vi.fn();
  render(<PrecheckReport draft={value} disabled={false} onCommit={onCommit} />);
  const commit = screen.getByRole('button', { name: '启动此文件入库' }) as HTMLButtonElement;
  expect(commit.disabled).toBe(false);
  fireEvent.click(commit);
  expect(onCommit).toHaveBeenCalledWith([]);
});
