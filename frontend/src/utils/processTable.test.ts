import { describe, expect, it } from 'vitest';
import { applyFilters, fmtValue, sortRows, toCsv, type Filters } from './processTable';
import type { ProcessFieldMeta } from '../types';

const fields: ProcessFieldMeta[] = [
  { name: 'run_no', label: '编号', unit: null, kind: 'text' },
  { name: 'thk_nm', label: '厚度', unit: 'nm', kind: 'numeric' },
];

const rows = [
  { run_no: 'F0425LW1', thk_nm: 395.3 },
  { run_no: 'F0425LW2', thk_nm: null },
  { run_no: 'F0518HW1', thk_nm: 130.8 },
];

describe('applyFilters', () => {
  it('无筛选条件时返回全部行', () => {
    expect(applyFilters(rows, fields, {})).toHaveLength(3);
  });

  it('数值 min/max 过滤，null 值被排除', () => {
    const filters: Filters = { thk_nm: { min: '200' } };
    const result = applyFilters(rows, fields, filters);
    expect(result.map((r) => r.run_no)).toEqual(['F0425LW1']);
    const ranged = applyFilters(rows, fields, { thk_nm: { min: '100', max: '400' } });
    expect(ranged).toHaveLength(2);
  });

  it('空字符串条件不生效', () => {
    expect(applyFilters(rows, fields, { thk_nm: { min: '', max: '' } })).toHaveLength(3);
  });

  it('文本包含匹配（大小写不敏感）', () => {
    const result = applyFilters(rows, fields, { run_no: { text: 'hw' } });
    expect(result.map((r) => r.run_no)).toEqual(['F0518HW1']);
  });
});

describe('sortRows', () => {
  it('数值升序，null 恒排最后', () => {
    const sorted = sortRows(rows, 'thk_nm', 'asc');
    expect(sorted.map((r) => r.run_no)).toEqual(['F0518HW1', 'F0425LW1', 'F0425LW2']);
  });

  it('数值降序，null 恒排最后', () => {
    const sorted = sortRows(rows, 'thk_nm', 'desc');
    expect(sorted.map((r) => r.run_no)).toEqual(['F0425LW1', 'F0518HW1', 'F0425LW2']);
  });

  it('文本排序', () => {
    const sorted = sortRows(rows, 'run_no', 'asc');
    expect(sorted.map((r) => r.run_no)).toEqual(['F0425LW1', 'F0425LW2', 'F0518HW1']);
  });

  it('sortKey 为 null 时原样返回', () => {
    expect(sortRows(rows, null, 'asc')).toBe(rows);
  });
});

describe('toCsv', () => {
  it('生成带 BOM、带单位表头的 CSV', () => {
    const csv = toCsv(rows.slice(0, 1), fields);
    expect(csv.charCodeAt(0)).toBe(0xfeff);
    expect(csv).toContain('编号,厚度(nm)');
    expect(csv).toContain('F0425LW1,395.3');
  });

  it('转义逗号/引号/换行，null 输出空串', () => {
    const csv = toCsv([{ run_no: 'a,"b"\nc', thk_nm: null }], fields);
    expect(csv).toContain('"a,""b""\nc",');
    expect(csv.trimEnd().endsWith(',')).toBe(true);
  });
});

describe('fmtValue', () => {
  it('整数原样，小数保留 4 位，空值输出占位符', () => {
    expect(fmtValue(98)).toBe('98');
    expect(fmtValue(24.199416909)).toBe('24.1994');
    expect(fmtValue(null)).toBe('—');
    expect(fmtValue('F0425LW1')).toBe('F0425LW1');
  });
});
