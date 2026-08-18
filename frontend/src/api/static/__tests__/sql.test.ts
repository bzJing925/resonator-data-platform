import { describe, it, expect } from 'vitest';
import { filtersToWhere } from '../index';

describe('filtersToWhere（树形格式，对齐后端 /api/query）', () => {
  it('空 → 恒真', () => {
    expect(filtersToWhere({})).toBe('1=1');
    expect(filtersToWhere({ op: 'and', children: [] })).toBe('1=1');
  });
  it('单叶 eq', () => {
    expect(filtersToWhere({ op: 'and', children: [{ field: 'pf', op: 'eq', value: 'Y' }] })).toBe(
      `("pf" = 'Y')`,
    );
  });
  it('between + or 组合', () => {
    const sql = filtersToWhere({
      op: 'or',
      children: [
        { field: 'fs_ghz', op: 'between', value: [4.5, 5] },
        { field: 'qs', op: 'gt', value: 800 },
      ],
    });
    expect(sql).toBe(`(("fs_ghz" >= 4.5 AND "fs_ghz" <= 5) OR "qs" > 800)`);
  });
  it('in / is_null / contains', () => {
    expect(filtersToWhere({ op: 'and', children: [{ field: 'eg', op: 'in', value: [0, 0.75] }] })).toBe(
      '("eg" IN (0, 0.75))',
    );
    expect(filtersToWhere({ field: 'wafer', op: 'is_null' })).toBe('"wafer" IS NULL');
    expect(filtersToWhere({ field: 'mark', op: 'contains', value: 'A_1' })).toBe(
      `"mark" LIKE '%A\\_1%' ESCAPE '\\'`,
    );
  });
  it('SQL 注入字符转义', () => {
    expect(filtersToWhere({ field: 'batch_no', op: 'eq', value: "x' OR 1=1 --" })).toBe(
      `"batch_no" = 'x'' OR 1=1 --'`,
    );
    expect(() => filtersToWhere({ field: 'x; DROP TABLE', op: 'eq', value: 1 })).toThrow();
  });
  it('旧版按字段格式兼容', () => {
    expect(filtersToWhere({ batch_no: ['#2', '01'] })).toBe(`("batch_no" IN ('#2', '01'))`);
    expect(filtersToWhere({ fs_ghz: { gte: 4.5, lt: 5 } })).toBe(
      '("fs_ghz" >= 4.5 AND "fs_ghz" < 5)',
    );
    expect(filtersToWhere({ pf: 'Y' })).toBe(`("pf" = 'Y')`);
  });
});
