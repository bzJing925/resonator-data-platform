import React, { useEffect, useState } from 'react';
import { getBatchSyncStatus, unknownSyncStatus } from './syncStatusAdapter';
import type { SyncStatus } from '../types/platformContracts';
const labels = { local_only: '仅本地', pending: '待确认', syncing: '同步中', confirmed: '已确认', conflict: '冲突', failed: '失败', unknown: '未知' };
export default function SyncStatusBadge({ batchNo }: { batchNo: string }) {
  const [statuses, setStatuses] = useState<{ server: SyncStatus } | null>(null), [refresh, setRefresh] = useState(0);
  useEffect(() => { let alive = true; setStatuses(null); void getBatchSyncStatus(batchNo).then((result) => { if (alive) setStatuses(result); }).catch(() => { if (alive) setStatuses({ server: unknownSyncStatus('server', 'STATUS_UNAVAILABLE') }); }); return () => { alive = false; }; }, [batchNo, refresh]);
  return <div aria-label="持久同步状态">{(['server'] as const).map((target) => {
    const status = statuses?.[target] ?? unknownSyncStatus(target);
    return <span key={target} style={{ marginRight: 12 }}>服务器：{labels[status.state]} · 确认版本 {status.confirmed_revision ?? '未知'} · 待确认版本 {status.pending_revision ?? '无'}{status.error_code && ` · ${status.error_code}`}{status.last_success_at && ` · ${status.last_success_at}`}</span>;
  })}<button onClick={() => setRefresh((value) => value + 1)}>刷新同步状态</button></div>;
}
