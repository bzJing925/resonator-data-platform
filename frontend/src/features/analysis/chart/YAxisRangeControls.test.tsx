import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import YAxisRangeControls from './YAxisRangeControls';

describe('numeric Y axis controls', () => {
  it('applies only on submit, rejects reversed bounds and supports reset', () => {
    const onApply = vi.fn(), onReset = vi.fn();
    render(<YAxisRangeControls fields={[{ name: 'qs', label: 'Qs' }]}
      ranges={{ qs: [95, 121] }} onApply={onApply} onReset={onReset} />);
    fireEvent.change(screen.getByLabelText('Qs Y轴最小值'), { target: { value: '130' } });
    expect(onApply).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '应用Qs Y轴范围' }));
    expect(screen.getByRole('alert').textContent).toContain('最小值须小于最大值');
    expect(onApply).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('Qs Y轴最小值'), { target: { value: '100.5' } });
    fireEvent.click(screen.getByRole('button', { name: '应用Qs Y轴范围' }));
    expect(onApply).toHaveBeenCalledWith('qs', [100.5, 121]);
    fireEvent.click(screen.getByRole('button', { name: '恢复自动Y轴范围' }));
    expect(onReset).toHaveBeenCalledOnce();
  });
});
