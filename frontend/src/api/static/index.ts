/** 静态模式 API 实现：DuckDB-WASM（parquet）+ fetch JSON（规格 §4）。
 *  与 endpoints.ts 的 HTTP 版同签名。 */

import { getConn, ensureTable, q, normalizeRow } from './duckdb';

async function fetchJson(path: string) {
  const r = await fetch(`webdata/${path}`);
  if (!r.ok) throw new Error(`快照缺失: ${path}`);
  return r.json();
}

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

async function runQuery(sql: string): Promise<any[]> {
  const conn = await getConn();
  const t = await conn.query(sql);
  return t.toArray().map((r) => normalizeRow(r.toJSON()));
}

// ---- 系统 ----
export const getHealth = async () => ({ status: 'ok', db: 'static', redis: 'static' });
export const getStats = () => fetchJson('stats.json');
export const listTasks = async () => [];

// ---- 工艺 ----
export const listProcessSteps = async () => {
  const p = await fetchJson('process.json');
  return Object.entries(p).map(([key, v]: [string, any]) => ({
    key,
    label: v.label,
    row_count: v.rows.length,
  }));
};
export const getProcessStep = async (step: string) => {
  const p = await fetchJson('process.json');
  const s = p[step];
  if (!s) throw new Error(`未知工序: ${step}`);
  return { fields: s.fields, rows: s.rows };
};

// ---- 批次 ----
const BATCH_SORT_FIELDS = new Set(['uploaded_at', 'batch_no', 'device_count']);

export const listBatches = async (params?: Record<string, unknown>) => {
  const page = Number(params?.page ?? 1);
  const size = Number(params?.size ?? 20);
  const sort = String(params?.sort ?? '-uploaded_at');
  const desc = sort.startsWith('-');
  const key = sort.replace(/^[-+]/, '');
  const items = (await fetchJson('batches.json')) as any[];
  const mappings = (await fetchJson('mappings.json')) as any[];
  const nameById = new Map(mappings.map((m) => [m.id, m.name]));
  let rows = items.map((b) => ({
    batch_no: b.batch_no,
    mapping_name: nameById.get(b.mapping_id) ?? null,
    device_count: b.device_count,
    f_start_ghz: b.f_start_ghz,
    f_end_ghz: b.f_end_ghz,
    deembedded: !!b.deembedded,
    deembed_method: b.deembed_method,
    process_type: b.process_type,
    uploaded_at: b.uploaded_at,
  }));
  if (BATCH_SORT_FIELDS.has(key)) {
    rows = rows.sort((a, b) => {
      const va = (a as any)[key];
      const vb = (b as any)[key];
      const cmp = va == null ? -1 : vb == null ? 1 : va < vb ? -1 : va > vb ? 1 : 0;
      return desc ? -cmp : cmp;
    });
  }
  const start = (page - 1) * size;
  return { total: rows.length, page, size, items: rows.slice(start, start + size) };
};

export const getBatch = async (batchNo: string) => {
  const items = (await fetchJson('batches.json')) as any[];
  const b = items.find((x) => x.batch_no === batchNo);
  if (!b) throw new Error(`批次不存在: ${batchNo}`);
  const mappings = (await fetchJson('mappings.json')) as any[];
  const mapping = mappings.find((m) => m.id === b.mapping_id);

  await ensureTable('devices');
  const statsRows = await runQuery(
    `SELECT AVG(fs_ghz) AS m, quantile_cont(fs_ghz, 0.5) AS med,`
    + ` SUM(CASE WHEN pf='Y' THEN 1 ELSE 0 END) AS p`
    + ` FROM devices WHERE batch_no = ${q(batchNo)}`,
  );
  const wafersRows = await runQuery(
    `SELECT DISTINCT wafer FROM devices WHERE batch_no = ${q(batchNo)}`
    + ` AND wafer IS NOT NULL ORDER BY wafer`,
  );
  const s = statsRows[0] ?? {};
  return {
    batch_no: b.batch_no,
    mapping_id: b.mapping_id,
    mapping_name: mapping?.name ?? null,
    device_count: b.device_count,
    f_start_ghz: b.f_start_ghz,
    f_end_ghz: b.f_end_ghz,
    deembedded: !!b.deembedded,
    deembed_method: b.deembed_method,
    process_type: b.process_type,
    uploaded_at: b.uploaded_at,
    uploaded_by: b.uploaded_by,
    task_id: b.task_id,
    wafers: wafersRows.map((r) => r.wafer),
    stats: {
      fs_ghz_mean: s.m ?? null,
      fs_ghz_median: s.med ?? null,
      pass_rate: b.device_count > 0 ? (Number(s.p ?? 0) / b.device_count) : null,
    },
  };
};

// ---- 批次内器件 ----
export const listBatchDevices = async (
  batchNo: string,
  params: Record<string, unknown> = {},
) => {
  const page = Number(params.page ?? 1);
  const size = Number(params.size ?? 100);
  const conds: string[] = [`batch_no = ${q(batchNo)}`];
  if (params.wafer != null) conds.push(`wafer = ${q(params.wafer as string | number)}`);
  if (params.pf != null) conds.push(`pf = ${q(params.pf as string | number)}`);

  // massloading：经由批次对照表的 has_pf 标记展开为 mark 集合
  const items = (await fetchJson('batches.json')) as any[];
  const b = items.find((x) => x.batch_no === batchNo);
  if (!b) throw new Error(`批次不存在: ${batchNo}`);
  let markToDesc = new Map<string, any>();
  if (b.mapping_id != null) {
    const mappings = (await fetchJson('mappings.json')) as any[];
    const mapping = mappings.find((m) => m.id === b.mapping_id);
    if (mapping) {
      markToDesc = new Map((mapping.entries ?? []).map((e: any) => [e.mark, e.description]));
      if (params.massloading != null) {
        const want = params.massloading === 'Y';
        const marks = (mapping.entries ?? [])
          .filter((e: any) => !!e.has_pf === want)
          .map((e: any) => e.mark);
        conds.push(marks.length ? `mark IN (${marks.map(q).join(', ')})` : '1=0');
      }
    }
  } else if (params.massloading != null) {
    conds.push('1=0');
  }

  await ensureTable('devices');
  const where = conds.join(' AND ');
  const cnt = await runQuery(`SELECT COUNT(*) AS c FROM devices WHERE ${where}`);
  const rows = await runQuery(
    `SELECT * FROM devices WHERE ${where} ORDER BY id LIMIT ${size} OFFSET ${(page - 1) * size}`,
  );
  for (const r of rows) {
    r.description = r.mark ? (markToDesc.get(r.mark as string) ?? null) : null;
  }
  return { items: rows, total: Number(cnt[0].c), page, size };
};

// ---- 对照表 ----
export const listMappings = async () => {
  const ms = (await fetchJson('mappings.json')) as any[];
  const batches = (await fetchJson('batches.json')) as any[];
  const useCount = new Map<number, number>();
  for (const b of batches) {
    if (b.mapping_id != null) useCount.set(b.mapping_id, (useCount.get(b.mapping_id) ?? 0) + 1);
  }
  return ms.map((m) => ({
    id: m.id,
    name: m.name,
    entry_count: m.entry_count ?? m.entries?.length ?? 0,
    in_use_by_batches: useCount.get(m.id) ?? 0,
    uploaded_at: m.uploaded_at,
  }));
};
export const listMappingEntries = async (
  id: number | string,
  params?: Record<string, unknown>,
) => {
  const ms = (await fetchJson('mappings.json')) as any[];
  const m = ms.find((x) => String(x.id) === String(id));
  const entries = (m?.entries ?? []).map((e: any) => ({ ...e, has_pf: !!e.has_pf }));
  const page = Number(params?.page ?? 1);
  const size = Number(params?.size ?? (entries.length || 1));
  const start = (page - 1) * size;
  return { items: entries.slice(start, start + size), total: entries.length, page, size };
};
// ---- 查询 ----
export const getQueryFields = () => fetchJson('fields.json');

export const getQueryDistinct = async (field: string, limit = 500) => {
  await ensureTable('devices');
  const col = fieldName(field);
  const rows = await runQuery(
    `SELECT DISTINCT ${col} AS v FROM devices WHERE ${col} IS NOT NULL`
    + ` ORDER BY ${col} LIMIT ${limit + 1}`,
  );
  return { values: rows.slice(0, limit).map((r) => r.v), truncated: rows.length > limit };
};

export interface QueryBody {
  filters?: Record<string, any>;
  fields?: string[];
  limit?: number;
  order_by?: string;
  skip_count?: boolean;
  page?: number;
  size?: number;
}

export async function queryDevices(body: QueryBody) {
  await ensureTable('devices');
  const where = filtersToWhere(body.filters ?? {});
  const cols = body.fields?.length ? body.fields.map(fieldName).join(', ') : '*';
  const limit = Math.min(body.limit ?? 10000, 200000);
  const order = body.order_by
    ? ` ORDER BY ${fieldName(body.order_by.replace(/^-/, ''))}${body.order_by.startsWith('-') ? ' DESC' : ''}`
    : '';
  const rows = await runQuery(`SELECT ${cols} FROM devices WHERE ${where}${order} LIMIT ${limit}`);
  let total: number | null = null;
  if (!body.skip_count) {
    const cnt = await runQuery(`SELECT COUNT(*) AS c FROM devices WHERE ${where}`);
    total = Number(cnt[0].c);
  }
  return {
    total,
    returned: rows.length,
    truncated: rows.length >= limit,
    sampled: false,
    sample_rate: 1,
    rows,
  };
}

const AGG_SQL: Record<string, (f: string) => string> = {
  count: (f) => `COUNT(${f})`,
  min: (f) => `MIN(${f})`,
  max: (f) => `MAX(${f})`,
  avg: (f) => `AVG(${f})`,
  sum: (f) => `SUM(${f})`,
  p25: (f) => `quantile_cont(${f}, 0.25)`,
  p50: (f) => `quantile_cont(${f}, 0.5)`,
  p75: (f) => `quantile_cont(${f}, 0.75)`,
};

export async function queryAggregate(body: {
  filters?: Record<string, any>;
  group_by: string[];
  metrics: { field: string; agg: string[] }[];
}) {
  await ensureTable('devices');
  const where = filtersToWhere(body.filters ?? {});
  const gb = body.group_by.map(fieldName);
  const aggs = body.metrics.flatMap((m) =>
    m.agg.map((a) => {
      const fn = AGG_SQL[a];
      if (!fn) throw new Error(`不支持的聚合操作: ${a}`);
      return `${fn(fieldName(m.field))} AS "${m.field}__${a}"`;
    }));
  const rows = await runQuery(
    `SELECT ${gb.join(', ')}, ${aggs.join(', ')} FROM devices WHERE ${where}`
    + ` GROUP BY ${gb.join(', ')}`,
  );
  const groups = rows.map((r) => {
    const g: Record<string, any> = {};
    for (const k of body.group_by) g[k] = r[k];
    for (const m of body.metrics) {
      g[m.field] = {};
      for (const a of m.agg) g[m.field][a] = r[`${m.field}__${a}`];
    }
    return g;
  });
  return { groups };
}

// ---- 导出（客户端生成 Blob；与 HTTP 版 responseType:'blob' 同形 {data: Blob}） ----
export async function exportCsv(body: QueryBody) {
  const res = await queryDevices({ ...body, limit: 200000 });
  const rows = res.rows;
  const cols = body.fields?.length ? body.fields : rows.length ? Object.keys(rows[0]) : [];
  const csv = [
    cols.join(','),
    ...rows.map((r) => cols.map((c) => JSON.stringify(r[c] ?? '')).join(',')),
  ].join('\n');
  return { data: new Blob(['﻿' + csv], { type: 'text/csv' }) };
}

// ---- 代理模型 ----
export const getSurrogateStatus = async () => {
  try {
    const [m, meta] = await Promise.all([
      fetchJson('manifest.json'),
      fetchJson('surr_meta.json'),
    ]);
    if (m.missing?.includes('surr_meta.json')) {
      return { available: false, reason: '模型数据未同步' };
    }
    await ensureTable('cell_means');
    const cm = (
      await runQuery(
        'SELECT MIN(eg) AS e0, MAX(eg) AS e1, MIN(fl) AS f0, MAX(fl) AS f1 FROM cell_means',
      )
    )[0];
    const areaRows = await runQuery('SELECT DISTINCT area FROM cell_means ORDER BY area');
    return {
      available: true,
      eg_min: cm.e0,
      eg_max: cm.e1,
      fl_min: cm.f0,
      fl_max: cm.f1,
      areas: areaRows.map((r) => Number(r.area)),
    };
  } catch (e) {
    return { available: false, reason: String(e) };
  }
};

export const predictSurrogate = async (params: {
  eg: number;
  fl: number;
  area: number;
  pf: number;
  ag: number;
}) => {
  const { surrogatePredictOnnx } = await import('../../ml/surrogateOnnx');
  const r = await surrogatePredictOnnx(params.eg, params.fl, params.area, params.pf, params.ag);
  // 同格真值（格均值谱，长表按格查询；eg/fl 源自 float32 数据集，用容差匹配防加宽误差）
  let trueCurve: number[] | undefined;
  try {
    await ensureTable('cell_means');
    const rows = await runQuery(
      `SELECT idx, z FROM cell_means WHERE abs(eg - ${q(params.eg)}) < 1e-6`
      + ` AND abs(fl - ${q(params.fl)}) < 1e-6`
      + ` AND area=${q(params.area)} AND pf=${q(params.pf)} ORDER BY idx`,
    );
    if (rows.length === r.freq_ghz.length) trueCurve = rows.map((x) => x.z);
  } catch {
    trueCurve = undefined;
  }
  return {
    params: r.params,
    freq: r.freq_ghz,
    z: r.z_db,
    z_base: r.z_base,
    sample_freq: r.sample_freq,
    sample_z: r.sample_z,
    in_grid: !!trueCurve,
    true_curves: trueCurve ? [trueCurve] : undefined,
    true_mean: undefined,
  };
};
