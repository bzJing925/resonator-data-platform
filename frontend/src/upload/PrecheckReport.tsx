import TechnicalDetails from '../components/TechnicalDetails';
import React, { useEffect, useState } from 'react';
import type { UploadDraft } from './types';
export default function PrecheckReport({ draft, disabled, onCommit }: { draft: UploadDraft; disabled: boolean; onCommit: (codes: string[]) => void }) {
  const [acknowledged, setAcknowledged] = useState<string[]>([]);
  useEffect(() => setAcknowledged([]), [draft.check_digest]);
  const warnings = draft.report.warning_codes ?? [], items = draft.report.items ?? [];
  const duts = items.filter((item) => item.classification === 'dut' && item.status === 'success');
  const fatal = (draft.report.fatal_count ?? 0) > 0;
  const options = draft.report.options;
  const frequency = (value: unknown) => typeof value === 'number' ? `${value} GHz` : '自动';
  return <div><p>检查结果：可处理文件 {duts.length}，器件/端口 {duts.reduce((n, item) => n + item.ports.length, 0)}；质量 {{ complete: '完整', partial: '含警告', invalid: '无有效结果' }[draft.report.quality_status ?? ''] ?? '未知'}</p>
    <p className="dim">频段：{options?.f_start_ghz == null && options?.f_end_ghz == null ? '自动（按各文件实际范围）' : `${frequency(options?.f_start_ghz)} – ${frequency(options?.f_end_ghz)}`}</p>
    <TechnicalDetails value={draft.report.options ?? {}} label="处理设置" />
    <ul>{(draft.report.issues ?? []).map((issue, index) => <li key={`${issue.code}:${index}`}>{issue.severity} · {issue.code} · {issue.message} {issue.relpath}</li>)}</ul>
    {warnings.map((code) => <label key={code} style={{ display: 'block' }}><input type="checkbox" checked={acknowledged.includes(code)} onChange={(event) => setAcknowledged((current) => event.target.checked ? [...current, code] : current.filter((value) => value !== code))} />已审查警告 {code}</label>)}
    {fatal && <p role="alert">存在致命问题，禁止启动入库。</p>}
    <button className="btn" disabled={disabled || fatal || draft.status !== 'ready' || warnings.some((code) => !acknowledged.includes(code))} onClick={() => onCommit(acknowledged)}>启动此文件入库</button>
  </div>;
}
