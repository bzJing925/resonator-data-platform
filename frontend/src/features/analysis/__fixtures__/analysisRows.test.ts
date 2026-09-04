import { describe, expect, it } from 'vitest';
import {
  ANALYSIS_CHART_TYPES,
  ANALYSIS_ROWS,
  ANALYSIS_Y_FIELDS,
} from './analysisRows';

describe('Phase 0 分析交互 fixture', () => {
  it('覆盖五类图形、三个 Y 指标、多个结构类型和面积', () => {
    expect(ANALYSIS_CHART_TYPES).toEqual(['scatter', 'box', 'violin', 'swarm', 'line']);
    expect(ANALYSIS_Y_FIELDS).toHaveLength(3);
    expect(new Set(ANALYSIS_ROWS.map((row) => row.structureType)).size).toBeGreaterThan(1);
    expect(new Set(ANALYSIS_ROWS.map((row) => row.areaReality)).size).toBeGreaterThan(1);
  });

  it('每行有唯一稳定 rowId，且面积源字段与规范字段一致', () => {
    const rowIds = ANALYSIS_ROWS.map((row) => row.rowId);
    expect(new Set(rowIds).size).toBe(ANALYSIS_ROWS.length);
    expect(ANALYSIS_ROWS.every((row) => row.__rowId === row.rowId)).toBe(true);
    expect(ANALYSIS_ROWS.every((row) => row.area_um2 === row.areaReality)).toBe(true);
  });
});
