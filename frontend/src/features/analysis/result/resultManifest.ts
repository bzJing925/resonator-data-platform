import type { QuerySpec, ResultManifest } from '../../../types/platformContracts';
import { parseManifest } from '../model/specCodec';
import { attachSourceRefs } from '../model/provenance';

export function unverifiedManifest(spec: QuerySpec, returned: number, total: number | null = null, truncated = false): ResultManifest {
  return { schema_version: 1, query_spec: structuredClone(spec), source_refs: [], matched_total: total,
    total_relation: total === null ? 'unknown' : 'exact', returned, truncated,
    sampling: { method: 'none', rate: null }, local_edits: 0, excluded_rows: 0,
    generated_at: new Date().toISOString(), consistency: 'unverified' };
}
export function validateQueryResult(input: unknown, spec: QuerySpec, aggregate = false) {
  if (!input || typeof input !== 'object') throw new Error('查询结果格式无效');
  const data = Object.fromEntries(Object.entries(input));
  const rows = aggregate ? data.groups : data.rows;
  if (!Array.isArray(rows) || rows.some((r) => !r || typeof r !== 'object' || Array.isArray(r))) throw new Error('查询行格式无效');
  const manifest = data.manifest ? parseManifest(data.manifest) : unverifiedManifest(spec, rows.length, null, data.truncated === true);
  if (!data.manifest && data.sampled === true) {
    if (typeof data.sample_rate !== 'number' || !Number.isFinite(data.sample_rate) || data.sample_rate <= 0 || data.sample_rate > 1) throw new Error('采样率未验证');
    manifest.sampling = { method: 'systematic', rate: data.sample_rate };
  }
  if (manifest.returned !== rows.length || (!aggregate && data.returned !== undefined && data.returned !== rows.length)) throw new Error('查询计数与行数不一致');
  if (manifest.local_edits || manifest.excluded_rows) throw new Error('源查询不能带本地修改账本');
  if (manifest.matched_total !== null && manifest.matched_total < rows.length) throw new Error('匹配总数小于返回行数');
  if (manifest.sampling.method === 'none' && manifest.sampling.rate !== null) throw new Error('未采样结果不能带采样率');
  if (manifest.sampling.method === 'systematic' && manifest.sampling.rate === null) throw new Error('采样结果缺少采样率');
  if (!aggregate && manifest.total_relation === 'exact' && !manifest.truncated && manifest.sampling.method === 'none' && manifest.matched_total !== rows.length) throw new Error('完整结果计数不一致');
  if (data.truncated !== undefined && manifest.truncated !== data.truncated) throw new Error('截断说明不一致');
  if (manifest.consistency === 'verified' && !aggregate && attachSourceRefs(rows, manifest).some((ref) => !ref || ref.source_revision === null || ref.source_device_key === null)) throw new Error('来源与结果版本不一致');
  if (canonical(manifest.query_spec.filters) !== canonical(spec.filters)
    || manifest.query_spec.order_by !== spec.order_by
    || manifest.query_spec.fields.length !== spec.fields.length
    || manifest.query_spec.fields.some((f) => !spec.fields.includes(f))) throw new Error('返回的查询规格不一致');
  return { rows: rows.map((row) => Object.fromEntries(Object.entries(row))), manifest };
}
/** Only the latest requested query may change rows, errors or loading state. */
export class RequestSequence {
  private value = 0;
  begin() { return ++this.value; }
  current(sequence: number) { return sequence === this.value; }
  invalidate() { this.value++; }
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => `${JSON.stringify(k)}:${canonical(v)}`).join(',')}}`;
  return JSON.stringify(value) ?? 'null';
}
