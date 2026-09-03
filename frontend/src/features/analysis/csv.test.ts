import { describe, expect, it } from 'vitest';
import { serializeWorksheetCsv } from './csv';

describe('worksheet CSV export', () => {
  it('exports the current local rows and excludes internal row ids', () => {
    const csv = serializeWorksheetCsv([
      { __rowId: 'device:1', id: 1, structureType: 'Series', note: 'a,b' },
    ]);
    expect(csv).toBe('\ufeffid,structureType,note\r\n1,Series,"a,b"');
    expect(csv).not.toContain('__rowId');
  });
});
