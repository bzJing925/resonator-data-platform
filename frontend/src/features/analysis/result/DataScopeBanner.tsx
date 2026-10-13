import React from 'react';
import type { ResultManifest } from '../../../types/platformContracts';
export default function DataScopeBanner({ manifest, rows, aggregate = false }: { manifest: ResultManifest | null; rows: number; aggregate?: boolean }) {
  const total = manifest?.matched_total;
  return <div role="status" data-testid="data-scope-banner">
    {aggregate ? '聚合组' : '工作表'}：返回 {rows.toLocaleString()}，
    {total == null ? '总数未知' : `${manifest?.total_relation === 'lower_bound' ? '匹配至少' : '匹配总数'} ${total.toLocaleString()}`}
    {manifest?.truncated && ' · 范围受限（已截断）'}
    {manifest?.sampling.method === 'systematic' && ` · 已采样 ${Math.round((manifest.sampling.rate ?? 0) * 100)}%`}
    {manifest?.consistency !== 'verified' && ' · 来源未验证'}
    {!!manifest?.local_edits && ` · 本地修改 ${manifest.local_edits}`}
    {!!manifest?.excluded_rows && ` · 本地排除 ${manifest.excluded_rows}`}
  </div>;
}
