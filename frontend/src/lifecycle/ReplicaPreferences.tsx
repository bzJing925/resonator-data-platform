import React, { useEffect, useState } from 'react';
import { getReplicaPreferences, hideReplica } from '../api/endpoints';
import { useCapabilities } from '../api/capabilities';
import { uploadError } from '../upload/types';
export default function ReplicaPreferences() {
  const { capabilities } = useCapabilities();
  const [rows, setRows] = useState<Awaited<ReturnType<typeof getReplicaPreferences>>['items']>([]), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  useEffect(() => { let stopped = false; getReplicaPreferences().then((value) => { if (!stopped) setRows(value.items); }).catch((failure) => { if (!stopped) setError(uploadError(failure)); }); return () => { stopped = true; }; }, []);
  return <section><h2>隐藏的本地副本</h2><p>重新显示仅修改本地偏好，下一次同步读取服务器当前状态。</p>{rows.filter((row) => row.hidden).map((row) => <p key={`${row.source_ref.origin_id}:${row.source_ref.source_batch_id}`}>{row.batch_no ?? row.source_ref.source_batch_id}<button disabled={busy || !capabilities?.features.replication_v3} onClick={async () => { if (busy) return; setBusy(true); try { await hideReplica(row.source_ref, false); const result = await getReplicaPreferences(); setRows(result.items); } catch (failure) { setError(uploadError(failure)); } finally { setBusy(false); } }}>重新显示本地副本</button></p>)}{error && <p role="alert">{error}</p>}</section>;
}
