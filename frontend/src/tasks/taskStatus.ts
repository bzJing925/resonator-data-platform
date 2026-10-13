export const terminalTask = (status?: string) => ['success', 'failed', 'cancelled'].includes(status ?? '');
export const activeTask = (status?: string | null) => ['pending', 'running', 'cancel_requested'].includes(status ?? '');
export const taskLabel = (status?: string) => ({ pending: '排队中', running: '运行中', success: '成功', failed: '失败', cancel_requested: '正在停止', cancelled: '已停止' }[status ?? ''] ?? '状态未知');
export const stopExplanation = '停止处理会保留输入及已发布结果；只有执行器确认停止后才显示已停止。';
export interface TaskSnapshot { run_id?: string | null; event_seq?: number; status?: string; schema_version?: number; progress_pct?: number; stage_progress_pct?: number; stage?: string; progress_msg?: string; error_msg?: string; progress?: { pct?: number; stage_pct?: number; message?: string } }
export class TaskEventGate {
  run: string | null = null;
  sequence = 0;
  status = 'pending';
  accept(data: TaskSnapshot, authoritative = false) {
    if (terminalTask(this.status)) return false;
    if (data.run_id && this.run && data.run_id !== this.run) return false;
    if (!authoritative && this.run && (!data.run_id || data.schema_version !== 1 || (!Number.isSafeInteger(data.event_seq) || (data.event_seq ?? 0) < 1))) return false;
    if (!authoritative && data.event_seq != null && data.event_seq <= this.sequence) return false;
    if (this.status === 'cancel_requested' && !['cancel_requested', 'cancelled'].includes(data.status ?? '')) return false;
    if (this.status === 'running' && data.status === 'pending') return false;
    if (data.run_id) this.run = data.run_id;
    if (data.event_seq != null) this.sequence = data.event_seq;
    if (data.status) this.status = data.status;
    return true;
  }
}
