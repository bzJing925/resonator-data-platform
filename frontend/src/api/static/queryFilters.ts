import { q } from './duckdb';
// ---- filter → SQL（对齐后端 /api/query：树形 {op, children} + 叶 {field, op, value}，
//      兼容旧版按字段格式：数组=IN、对象=多操作符 AND、标量=eq） ----

const IDENT = /^[a-zA-Z_][a-zA-Z0-9_]*$/;

function fieldName(f: string): string {
  if (!IDENT.test(f)) throw new Error(`非法字段名: ${f}`);
  return `"${f}"`;
}

function escapeLike(s: string): string {
  return s.replaceAll('\\', '\\\\').replaceAll('%', '\\%').replaceAll('_', '\\_');
}

function leafToSql(field: string, op: string, val: any): string {
  const col = fieldName(field);
  switch (op) {
    case 'in':
      if (!Array.isArray(val) || !val.length) throw new Error(`${field}.in 必须是非空列表`);
      return `${col} IN (${val.map(q).join(', ')})`;
    case 'not_in':
      if (!Array.isArray(val) || !val.length) throw new Error(`${field}.not_in 必须是非空列表`);
      return `${col} NOT IN (${val.map(q).join(', ')})`;
    case 'eq':
      return `${col} = ${q(val)}`;
    case 'neq':
      return `${col} != ${q(val)}`;
    case 'gte':
      return `${col} >= ${q(val)}`;
    case 'gt':
      return `${col} > ${q(val)}`;
    case 'lte':
      return `${col} <= ${q(val)}`;
    case 'lt':
      return `${col} < ${q(val)}`;
    case 'between': {
      if (!Array.isArray(val) || val.length !== 2) throw new Error(`${field}.between 需 [lo, hi]`);
      return `(${col} >= ${q(val[0])} AND ${col} <= ${q(val[1])})`;
    }
    case 'is_null':
      return `${col} IS NULL`;
    case 'not_null':
      return `${col} IS NOT NULL`;
    case 'like':
      return `${col} LIKE '${escapeLike(String(val))}' ESCAPE '\\'`;
    case 'contains':
      return `${col} LIKE '%${escapeLike(String(val))}%' ESCAPE '\\'`;
    default:
      throw new Error(`不支持的操作符: ${field}.${op}`);
  }
}

function nodeToSql(node: any): string {
  if (node == null || typeof node !== 'object') return '1=1';
  if (node.field) {
    return leafToSql(node.field, node.op ?? 'eq', node.value);
  }
  if (Array.isArray(node.children)) {
    const parts = node.children.map(nodeToSql).filter((s: string) => s !== '1=1');
    if (!parts.length) return '1=1';
    const joiner = (node.op ?? 'and').toLowerCase() === 'or' ? ' OR ' : ' AND ';
    return `(${parts.join(joiner)})`;
  }
  return '1=1';
}

export function filtersToWhere(filters: any): string {
  if (!filters || typeof filters !== 'object') return '1=1';
  if (filters.field || Array.isArray(filters.children)) return nodeToSql(filters);
  // 旧版按字段格式
  const parts: string[] = [];
  for (const [field, spec] of Object.entries(filters)) {
    if (spec == null) continue;
    if (Array.isArray(spec)) {
      if (spec.length) parts.push(`(${fieldName(field)} IN (${spec.map(q).join(', ')}))`);
      continue;
    }
    if (typeof spec === 'object') {
      const subs = Object.entries(spec as Record<string, any>).map(([op, v]) =>
        leafToSql(field, op, v));
      if (subs.length) parts.push(`(${subs.join(' AND ')})`);
      continue;
    }
    parts.push(`(${fieldName(field)} = ${q(spec as string | number)})`);
  }
  return parts.length ? parts.join(' AND ') : '1=1';
}
