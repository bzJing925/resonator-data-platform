import { describe, expect, it } from 'vitest';
import { createAnalysisDataset, duplicateAnalysisDataset } from './dataset';

describe('analysis dataset model', () => {
  it('normalizes field aliases while preserving the source field map', () => {
    const dataset = createAnalysisDataset({
      id: 'source',
      name: 'source',
      kind: 'query',
      rows: [
        { id: 1, structure_type: ' Series ', area_um2: 700 },
        { id: 2, structure_type: null, area_um2: null },
      ],
    });
    expect(dataset.fieldMap).toEqual({
      structureType: 'structure_type',
      areaReality: 'area_um2',
    });
    expect(dataset.rows[0]).toMatchObject({
      rowId: 'source:device:1',
      __rowId: 'source:device:1',
      datasetId: 'source',
      structureType: 'Series',
      areaReality: 700,
    });
    expect(dataset.rows[1]).toMatchObject({ structureType: '', areaReality: null });
  });

  it('parses Area_reality numeric strings and rejects non-finite values', () => {
    const dataset = createAnalysisDataset({
      id: 'area',
      name: 'area',
      kind: 'query',
      rows: [{ Area_reality: '900' }, { Area_reality: 'invalid' }],
    });
    expect(dataset.rows.map((row) => row.areaReality)).toEqual([900, null]);
  });

  it('makes duplicate raw ids unique and keeps supplied row ids stable', () => {
    const dataset = createAnalysisDataset({
      id: 'stable',
      name: 'stable',
      kind: 'query',
      rows: [{ id: 1 }, { id: 1 }, { rowId: 'imported:7' }],
    });
    expect(dataset.rows.map((row) => row.rowId)).toEqual([
      'stable:device:1', 'stable:device:1:1', 'imported:7',
    ]);
  });

  it('duplicates rows with new ids without losing source mappings', () => {
    const source = createAnalysisDataset({
      id: 'source', name: 'source', kind: 'query', rows: [{ area_um2: 700 }],
    });
    const duplicate = duplicateAnalysisDataset(source, 'copy', 'copy');
    expect(duplicate.fieldMap).toEqual(source.fieldMap);
    expect(duplicate.rows[0]).toMatchObject({
      rowId: 'copy:row:0', datasetId: 'copy', areaReality: 700,
    });
    expect(duplicate.rows[0]).not.toBe(source.rows[0]);
  });
});
