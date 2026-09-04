import { describe, expect, it } from 'vitest';
import { buildColumnModel, columnGridTemplate, columnModelWidth } from './columnModel';

describe('worksheet column model', () => {
  it('defines system and data columns once for header and rows', () => {
    const model = buildColumnModel([
      { key: 'qs', sourceField: 'qs' },
      { key: 'long_measurement_name', sourceField: 'long_measurement_name' },
    ]);
    expect(model.map((column) => column.role)).toEqual([
      'row-number', 'selection', 'row-id', 'data', 'data',
    ]);
    expect(columnGridTemplate(model)).toBe('44px 34px 164px 120px 217px');
    expect(columnModelWidth(model)).toBe(579);
  });
});
