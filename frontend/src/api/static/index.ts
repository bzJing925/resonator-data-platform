/** 静态模式 API 实现：DuckDB-WASM（parquet）+ fetch JSON（规格 §4）。
 *  与 endpoints.ts 的 HTTP 版同签名。 */

import { publishedSources } from './comparison';
import { COMPARISON_METRICS } from '../../features/analysis/comparison/comparisonSpec';
import { unverifiedManifest } from '../../features/analysis/result/resultManifest';
import { getConn, ensureTable, q, normalizeRow, assertDeviceSnapshot } from './duckdb';

async function fetchJson(path: string) {
  const r = await fetch(`webdata/${path}`);
  if (!r.ok) throw new Error(`快照缺失: ${path}`);
  return r.json();
}

export { filtersToWhere } from './queryFilters';
import { filtersToWhere } from './queryFilters';
function fieldName(f: string) { if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(f)) throw new Error(`非法字段名: ${f}`); return `\"${f}\"`; }

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
  await assertDeviceSnapshot();
  const where = filtersToWhere(body.filters ?? {});
  const sources = publishedSources();
  const selectedFields = body.fields?.length ? [...new Set([...body.fields, ...(sources.length ? ['batch_id', 'source_device_key'] : [])])] : null;
  const cols = selectedFields ? selectedFields.map(fieldName).join(', ') : '*';
  const limit = Math.min(body.limit ?? 10000, 200000);
  const order = body.order_by
    ? ` ORDER BY ${fieldName(body.order_by.replace(/^-/, ''))}${body.order_by.startsWith('-') ? ' DESC' : ''}`
    : '';
  const fetched = await runQuery(`SELECT ${cols} FROM devices WHERE ${where}${order} LIMIT ${limit + 1}`);
  const rows = fetched.slice(0, limit).map((row) => {
    const source = sources.find((record) => record.batch_id === row.batch_id)?.source;
    return source ? { ...row, _source_ref: { ...source, source_device_key: row.source_device_key } } : row;
  }), truncated = fetched.length > limit;
  let total: number | null = null;
  if (!body.skip_count) {
    const cnt = await runQuery(`SELECT COUNT(*) AS c FROM devices WHERE ${where}`);
    total = Number(cnt[0].c);
  }
  if (total === null && !truncated) total = rows.length;
  const matchedIds = sources.length ? await runQuery(`SELECT DISTINCT batch_id FROM devices WHERE ${where}`) : [];
  const matchedSources = matchedIds.map((id) => sources.find((source) => source.batch_id === id.batch_id));
  const metrics = body.fields?.filter((field) => COMPARISON_METRICS.includes(field)) ?? COMPARISON_METRICS;
  const verified = sources.length > 0 && matchedSources.every((source) => source && metrics.every((metric) => source.proven_metrics.includes(metric)))
    && rows.every((row) => typeof row._source_ref === 'object' && row._source_ref !== null);
  const manifest = { ...unverifiedManifest({ filters: body.filters ?? {}, fields: body.fields ?? [], order_by: body.order_by ?? null }, rows.length, total, truncated),
    source_refs: matchedSources.flatMap((source) => source ? [source.source] : []), consistency: verified ? 'verified' as const : 'unverified' as const };
  await assertDeviceSnapshot();
  return {
    manifest,
    total,
    returned: rows.length,
    truncated,
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
  await assertDeviceSnapshot();
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
  const count = await runQuery(`SELECT COUNT(*) AS c FROM devices WHERE ${where}`);
  await assertDeviceSnapshot();
  return { groups, manifest: unverifiedManifest({ filters: body.filters ?? {}, fields: [...new Set([...body.group_by, ...body.metrics.map((m) => m.field)])], order_by: null }, groups.length, Number(count[0].c)) };
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

