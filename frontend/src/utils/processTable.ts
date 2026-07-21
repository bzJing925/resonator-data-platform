import type { ProcessFieldMeta } from '../types';

export type Row = Record<string, string | number | null>;
export interface FilterCond {
  min?: string;
  max?: string;
  text?: string;
}
export interface Filters {
  [field: string]: FilterCond;
}
export type SortDir = 'asc' | 'desc';

/** 客户端筛选：数值列 min/max，文本列包含匹配；null 值在任何生效条件下都被排除。 */
export function applyFilters(rows: Row[], fields: ProcessFieldMeta[], filters: Filters): Row[] {
  return rows.filter((row) =>
    fields.every((f) => {
      const cond = filters[f.name];
      if (!cond) return true;
      const v = row[f.name];
      if (f.kind === 'numeric') {
        const hasMin = cond.min !== undefined && cond.min !== '';
        const hasMax = cond.max !== undefined && cond.max !== '';
        if (!hasMin && !hasMax) return true;
        if (v === null || v === undefined || v === '') return false;
        const num = Number(v);
        if (hasMin && num < Number(cond.min)) return false;
        if (hasMax && num > Number(cond.max)) return false;
        return true;
      }
      const t = (cond.text ?? '').trim().toLowerCase();
      if (!t) return true;
      return String(v ?? '')
        .toLowerCase()
        .includes(t);
    }),
  );
}

/** 客户端排序：null 恒排最后（与方向无关）。 */
export function sortRows(rows: Row[], key: string | null, dir: SortDir): Row[] {
  if (!key) return rows;
  const mul = dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const va = a[key];
    const vb = b[key];
    if (va === null || va === undefined) return 1;
    if (vb === null || vb === undefined) return -1;
    if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * mul;
    return String(va).localeCompare(String(vb), 'zh') * mul;
  });
}

/** 生成 CSV（含 BOM，Excel 可直接打开；表头带单位）。 */
export function toCsv(rows: Row[], fields: ProcessFieldMeta[]): string {
  const esc = (v: string | number | null | undefined): string => {
    if (v === null || v === undefined) return '';
    const s = String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const header = fields.map((f) => esc(f.unit ? `${f.label}(${f.unit})` : f.label)).join(',');
  const lines = rows.map((r) => fields.map((f) => esc(r[f.name])).join(','));
  return '\uFEFF' + [header, ...lines].join('\n');
}

/** 表格显示格式化：整数原样，小数最多 4 位，空值占位符。 */
export function fmtValue(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : String(+v.toFixed(4));
  return v;
}
