import { describe, expect, it } from 'vitest';
import { serializeWorksheetCsv, serializeWorksheetTsv } from './csv';

describe('worksheet CSV export', () => {
  it('exports the current local rows and excludes internal row ids', () => {
    const csv = serializeWorksheetCsv([
      {
        rowId: 'dataset:device:1',
        __rowId: 'dataset:device:1',
        datasetId: 'dataset',
        id: 1,
        structureType: 'Series',
        note: 'a,b',
      },
    ]);
    expect(csv).toBe('\ufeffid,structureType,note\r\n1,Series,"a,b"');
    expect(csv).not.toContain('__rowId');
    expect(csv).not.toContain('datasetId');
  });

  it('copies selected worksheet rows as spreadsheet-compatible TSV', () => {
    const tsv = serializeWorksheetTsv([{
      rowId: 'r1', datasetId: 'd1', id: 1, note: 'line\tone',
    }]);
    expect(tsv).toBe('id\tnote\r\n1\t"line\tone"');
    expect(tsv).not.toContain('rowId');
  });
});
