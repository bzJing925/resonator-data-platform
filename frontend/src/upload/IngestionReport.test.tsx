import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import IngestionReport from './IngestionReport';
import type { ProcessingReport } from './types';

vi.mock('../api/endpoints', () => ({
  getProcessingReport: vi.fn(),
  downloadProcessingIssues: vi.fn(),
}));

const report: ProcessingReport = {
  run_id: 'run-a', quality_status: 'complete',
  summary: { published_count: 0, file_counts: {}, device_counts: {}, issue_counts: { info: 2 } },
  issues: [
    { id: '1', code: 'MAPPING_NOT_FOUND', severity: 'info', message: '器件未命中对照表：入库但不参与分析', details: { source_member: 'D1/one.s2p' } },
    { id: '2', code: 'MAPPING_NOT_FOUND', severity: 'info', message: '器件未命中对照表：入库但不参与分析', details: { source_member: 'D2/two.s2p' } },
  ],
  next_offset: null,
};

it('collapses ingestion issues and groups device names inline', () => {
  const { container } = render(<MemoryRouter><IngestionReport taskId={2} report={report} status="cancel_requested" /></MemoryRouter>);
  const details = container.querySelector('.precheck-issues') as HTMLDetailsElement;
  expect(details.open).toBe(false);
  expect(container.querySelectorAll('ul')).toHaveLength(0);
  fireEvent.click(screen.getByText('检查问题（2 项）'));
  expect(details.open).toBe(true);
  const group = screen.getByText('器件未命中对照表').closest('p')!;
  expect(group.textContent).toBe('器件未命中对照表：one.s2p、two.s2p');
  expect(group.querySelectorAll('li, br')).toHaveLength(0);
  expect(screen.getByRole('button', { name: '下载全部问题CSV' })).toBeTruthy();
});
