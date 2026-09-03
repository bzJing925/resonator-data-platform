import { describe, expect, it } from 'vitest';
import { visibleRowRange } from './WorksheetGrid';

describe('worksheet virtualization', () => {
  it('only renders the viewport plus a small overscan window', () => {
    expect(visibleRowRange(2800, 560, 100_000)).toEqual({ start: 92, end: 128 });
  });

  it('clamps the window at dataset boundaries', () => {
    expect(visibleRowRange(0, 280, 12)).toEqual({ start: 0, end: 12 });
  });
});
