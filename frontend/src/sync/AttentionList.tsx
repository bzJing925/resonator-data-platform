import React, { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { listTasks, getTask } from '../api/endpoints';
import api from '../api/client';
import { getBatchSyncStatus } from './syncStatusAdapter';
import { useAnalysisWorkspace } from '../features/analysis/workspace';
import { validateSourceRefs } from '../features/analysis/model/provenance';
import type { SourceRef } from '../types/platformContracts';
export interface AttentionItem { id: string; code: string; text: string; href: string }
export default function AttentionList({ refresh = 0 }: { refresh?: number }) {
  const [tasks, setTasks] = useState<AttentionItem[] | null>(null), [quality, setQuality] = useState<AttentionItem[] | null>(null), [sync, setSync] = useState<AttentionItem[] | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [sourceError, setSourceError] = useState('');
  const { datasets } = useAnalysisWorkspace(), navigate = useNavigate();
  useEffect(() => {
    let alive = true; setErrors({});
    const fail = (key: string, failure: unknown) => { if (alive) setErrors((current) => ({ ...current, [key]: `未提供/加载失败：${String(failure)}` })); };
    void listTasks().then(async (response) => {
      const rows = Array.isArray(response) ? response : (response as any).items;
      if (!Array.isArray(rows)) throw new Error('任务列表无效');
      if (alive) setTasks(rows.filter((task) => ['failed', 'cancel_requested'].includes(task.status)).map((task) => ({ id: `task:${task.id}`, code: task.status === 'failed' ? 'TASK_FAILED' : 'STOP_PENDING', text: `任务 ${task.id} · ${task.batch_no ?? '未知批次'}`, href: `/tasks/${task.id}` })));
      const details = await Promise.allSettled(rows.slice(0, 10).map((task) => getTask(task.id)));
      if (!alive) return;
      const issues: AttentionItem[] = [];
      for (const [index, result] of details.entries()) {
        if (result.status === 'rejected') { fail('quality', result.reason); continue; }
        const task = result.value as any;
        if (!task.quality_status || task.quality_status === 'unknown') { fail('quality', `任务${rows[index].id}质量结果未提供`); continue; }
        if (['partial', 'invalid'].includes(task.quality_status)) issues.push({ id: `quality:${rows[index].id}`, code: `QUALITY_${task.quality_status.toUpperCase()}`, text: `任务 ${rows[index].id} · ${JSON.stringify(task.summary ?? {})}`, href: `/tasks/${rows[index].id}` });
      }
      if (!details.some((result) => result.status === 'rejected')) setQuality(issues);
      else setQuality(issues.length ? issues : null);
    }).catch((failure) => fail('tasks', failure));
    void api.get('/sync/v3/catalog').then(async ({ data }) => {
      if (data.protocol !== 3 || data.complete !== true || !Array.isArray(data.batches)) throw new Error('来源目录未提供');
      const rows = data.batches.filter((batch) => batch.state === 'active').sort((a, b) => b.id - a.id).slice(0, 10);
      if (rows.some((batch) => !validateSourceRefs(batch.source_ref))) throw new Error('来源身份未提供');
      const statuses = await Promise.allSettled(rows.map((batch) => getBatchSyncStatus(batch.batch_no)));
      if (!alive) return;
      const items: AttentionItem[] = [];
      for (const [index, result] of statuses.entries()) {
        if (result.status === 'rejected') { fail('sync', result.reason); continue; }
        for (const target of ['server'] as const) {
          const value = result.value[target];
          if (['failed', 'conflict', 'unknown'].includes(value.state)) items.push({ id: `sync:${rows[index].source_ref.origin_id}:${rows[index].source_ref.source_batch_id}:${target}`, code: value.error_code ?? `SYNC_${value.state.toUpperCase()}`, text: `${rows[index].batch_no} · ${target} · ${value.state}`, href: `/settings?target=${target}&batch=${encodeURIComponent(rows[index].batch_no)}` });
        }
      }
      setSync(statuses.some((result) => result.status === 'rejected') && !items.length ? null : items);
    }).catch((failure) => fail('sync', failure));
    return () => { alive = false; };
  }, [refresh]);
  const refs = new Map<string, SourceRef>();
  for (const dataset of datasets) for (const ref of dataset.manifest?.source_refs ?? []) if (validateSourceRefs(ref)) refs.set(`${ref.origin_id}:${ref.source_batch_id}`, ref);
  const source = async (ref: SourceRef) => {
    setSourceError('');
    try {
      const { data } = await api.get(`/sources/batches/${ref.origin_id}/${ref.source_batch_id}/resolve`);
      if (data.state !== 'active' || data.hidden || data.purged) throw new Error('源已删除、隐藏或清理；项目快照保留');
      navigate(`/batches/${encodeURIComponent(data.batch_no)}`);
    } catch (failure) { setSourceError((failure as any)?.response?.status === 404 ? '源已不存在；项目快照保留' : String(failure)); }
  };
  const section = (title: string, key: string, values: AttentionItem[] | null) => <section><h3>{title}</h3>{errors[key] && <p role="alert">{errors[key]}</p>}{values === null ? <p>未提供/加载中</p> : values.length ? <ul>{values.map((item) => <li key={item.id}><Link to={item.href}>{item.text} · {item.code}</Link></li>)}</ul> : errors[key] ? <p>范围内信息不完整，不能判断是否无异常</p> : <p>已读取范围内无异常</p>}</section>;
  return <div aria-label="需要处理">
    {section('异常任务（最近50项）', 'tasks', tasks)}{section('质量问题（最近10个任务）', 'quality', quality)}{section('同步问题（最近10个批次）', 'sync', sync)}
    <h3>当前项目源数据</h3>{[...refs.entries()].map(([id, ref]) => <button key={id} onClick={() => source(ref)}>{id} · 打开源批次</button>)}{sourceError && <p role="alert">{sourceError}</p>}
  </div>;
}
