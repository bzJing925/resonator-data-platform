import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import Process from './Process';

vi.mock('../api/endpoints', () => ({
  listProcessSteps: vi.fn().mockResolvedValue([
    { key: 'icp_etch', label: 'ICP 刻蚀', row_count: 2 },
    { key: 'mo_dep', label: 'Mo 生长', row_count: 1 },
  ]),
  getProcessStep: vi.fn().mockResolvedValue({
    fields: [
      { name: 'run_no', label: '编号', unit: null, kind: 'text' },
      { name: 'etch_time_sec', label: '刻蚀时间', unit: 's', kind: 'numeric' },
    ],
    rows: [
      { run_no: 'F0425LW1', etch_time_sec: 98 },
      { run_no: 'F0518HW3', etch_time_sec: 43 },
    ],
  }),
}));

describe('Process 页面', () => {
  it('渲染工序 tab 并加载首个工序数据', async () => {
    render(<Process />);
    expect(await screen.findByText('F0425LW1')).toBeTruthy();
    expect(screen.getByText('Mo 生长 (1)')).toBeTruthy();
    expect(screen.getByText('共 2 行')).toBeTruthy();
  });

  it('文本筛选过滤行', async () => {
    render(<Process />);
    await screen.findByText('F0425LW1');
    fireEvent.click(screen.getByTitle('筛选'));
    fireEvent.change(screen.getByPlaceholderText('编号 包含'), { target: { value: 'HW' } });
    await waitFor(() => {
      expect(screen.queryByText('F0425LW1')).toBeNull();
      expect(screen.getByText('F0518HW3')).toBeTruthy();
    });
  });

  it('点击表头切换排序', async () => {
    render(<Process />);
    await screen.findByText('F0425LW1');
    const firstDataRowText = () => screen.getAllByRole('row')[1].textContent;
    expect(firstDataRowText()).toContain('F0425LW1');
    fireEvent.click(screen.getByText(/刻蚀时间/)); // 升序：43 在前
    await waitFor(() => expect(firstDataRowText()).toContain('F0518HW3'));
    fireEvent.click(screen.getByText(/刻蚀时间/)); // 降序：98 在前
    await waitFor(() => expect(firstDataRowText()).toContain('F0425LW1'));
  });
});
