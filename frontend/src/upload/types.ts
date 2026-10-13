import type { Task } from '../types';
export interface UploadOptions { mapping_id: number; f_start_ghz: number | null; f_end_ghz: number | null; process_type: string; deembed: boolean; deembed_method: string; target_batch: string }
export interface PrecheckIssue { code: string; severity: string; message: string; relpath?: string }
export interface UploadDraft { draft_id: string; group_id: string; status: 'preparing' | 'ready' | 'failed' | 'consumed' | 'expired'; prepare_progress: number; check_digest: string | null; report: { prepare_stage?: string; prepare_completed?: number; prepare_total?: number | null; prepare_unit?: 'bytes' | 'files' | 'rows' | 'steps' | 'percent'; prepare_updated_at?: string; cancel_requested?: boolean; items?: { classification: string; ports: string[]; status: string }[]; issues?: PrecheckIssue[]; quality_status?: string; fatal_count?: number; warning_codes?: string[]; options?: Record<string, unknown> }; suggested_batch_no: string; expires_at: string; consumed_task_id: number | null; consumed_task: { task_id: string; batch_no: string; status: string; stage: string; progress_pct: number } | null }
export interface ProcessingReport { run_id: string | null; quality_status: string; summary: Record<string, any>; issues: { id: string; code: string; severity: string; message: string; details?: Record<string, unknown> }[]; next_offset: number | null }
export interface UploadEntry { key: string; name: string; size: number; modified: number; requestKey: string | null; draftId: string | null; optionsKey: string | null; draft?: UploadDraft; task?: Task; report?: ProcessingReport; phase?: 'selected' | 'uploading' | 'checking' | 'failed'; progress?: number; error?: string }
export function fileKey(file: File) { return JSON.stringify([file.name, file.size, file.lastModified]); }
export function uploadError(failure: any) { const detail = failure?.response?.data?.detail; return typeof detail === 'object' ? `${detail.code}: ${detail.message}` : String(detail ?? failure?.message ?? failure); }
export function validateSelection(files: File[], options: UploadOptions) {
  if (!Number.isSafeInteger(options.mapping_id) || options.mapping_id <= 0) throw new Error('请选择对照表');
  if (![options.f_start_ghz, options.f_end_ghz].every((value) => value === null || (Number.isFinite(value) && value > 0)) || (options.f_start_ghz !== null && options.f_end_ghz !== null && options.f_start_ghz >= options.f_end_ghz)) throw new Error('频率须为有限正数，起始频率须小于结束频率；留空自动使用文件范围');
  if (files.some((file) => !file.size)) throw new Error('零字节文件不能检查');
  if (new Set(files.map(fileKey)).size !== files.length) throw new Error('选择了相同名称/大小/修改时间文件，请核对；最终重复性以后端摘要为准');
}
