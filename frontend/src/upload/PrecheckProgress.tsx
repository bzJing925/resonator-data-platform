import React from 'react';
import type { UploadDraft } from './types';

const stages: Record<string, string> = {
  hash_input: '校验原始文件', validate_archive: '检查压缩包路径',
  validate_extract: '复核解压路径', extract: '解压文件',
  inspect_files: '检查数据文件', calibration: '检查去嵌校准',
  read_table: '读取表格', inspect_rows: '检查表格行', verify_input: '复核原始文件',
};
const numbers = new Intl.NumberFormat('zh-CN');
function bytes(value: number) {
  const unit = value >= 1024 ** 3 ? 'GB' : value >= 1024 ** 2 ? 'MB' : value >= 1024 ? 'KB' : 'B';
  const divisor = { GB: 1024 ** 3, MB: 1024 ** 2, KB: 1024, B: 1 }[unit];
  return `${numbers.format(Math.round(value / divisor * 10) / 10)} ${unit}`;
}

export default function PrecheckProgress({ draft }: { draft: UploadDraft }) {
  if (draft.status !== 'preparing') return null;
  const report = draft.report;
  const progress = Number.isFinite(draft.prepare_progress) ? Math.max(0, Math.min(99, draft.prepare_progress)) : 0;
  const stage = report.cancel_requested ? '正在停止检查' : stages[report.prepare_stage ?? ''] ?? (progress === 0 ? '等待检查' : '检查中');
  const done = report.prepare_completed, total = report.prepare_total;
  let detail = '';
  if (!report.cancel_requested && typeof done === 'number' && Number.isFinite(done) && done >= 0) {
    const hasTotal = typeof total === 'number' && Number.isFinite(total) && total > 0;
    const count = hasTotal ? Math.min(done, total) : done;
    if (report.prepare_unit === 'bytes') detail = hasTotal ? `${bytes(count)} / ${bytes(total)}` : bytes(count);
    else if (report.prepare_unit === 'percent') detail = hasTotal ? `解压 ${Math.round(count / total * 100)}%` : '';
    else if (['files', 'rows', 'steps'].includes(report.prepare_unit ?? '') && hasTotal) {
      const unit = report.prepare_unit === 'files' ? '个文件' : report.prepare_unit === 'rows' ? '行' : '项';
      detail = `${numbers.format(count)} / ${numbers.format(total)} ${unit}`;
    }
  }
  const label = `${stage} · 总进度 ${progress}%${detail ? ` · ${detail}` : ''}`;
  return <div className="precheck-progress">
    <p>{label}</p>
    <div className="precheck-progress-track" role="progressbar" aria-label="预检查总进度"
      aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress} aria-valuetext={label}>
      <div className="precheck-progress-fill" style={{ width: `${progress}%` }} />
    </div>
  </div>;
}
