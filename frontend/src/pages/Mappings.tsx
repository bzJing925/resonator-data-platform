import React, { memo, useCallback, useEffect, useRef, useState } from 'react';
import I from '../components/Icons';
import {
  listMappings,
  listMappingEntries,
  createMapping,
  deleteMapping,
} from '../api/endpoints';
import { usePageState } from '../contexts/PageStateContext';
import type { Mapping, MappingEntry } from '../types';

interface EntryRowProps {
  entry: MappingEntry;
}

const EntryRow = memo(function EntryRow({ entry }: EntryRowProps) {
  const e = entry;
  return (
    <tr>
      <td><b>{e.mark}</b></td>
      <td className="muted">{e.description}</td>
      <td className="num">{e.eg ?? '—'}</td>
      <td className="num">{e.fl ?? '—'}</td>
      <td className="num dim">{e.ag ?? '—'}</td>
      <td className="num">{e.area_s11 ?? '—'}</td>
      <td className="num">{e.area_s22 ?? '—'}</td>
      <td>{e.has_pf ? '是' : '—'}</td>
    </tr>
  );
});

const MAPPINGS_INITIAL_STATE = {
  selected: null,
  name: '',
};

export default function Mappings() {
  const [state, setState] = usePageState('mappings', MAPPINGS_INITIAL_STATE);
  const { selected, name } = state;
  const setSelected = useCallback((v: number | string | null | ((prev: number | string | null) => number | string | null)) => setState((s) => ({ ...s, selected: typeof v === 'function' ? v(s.selected) : v })), [setState]);
  const setName = useCallback((v: string | ((prev: string) => string)) => setState((s) => ({ ...s, name: typeof v === 'function' ? v(s.name) : v })), [setState]);

  const [mappings, setMappings] = useState<Mapping[]>([]);
  const [entries, setEntries] = useState<{ items: MappingEntry[]; total: number }>({ items: [], total: 0 });
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState<boolean>(false);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const loadMappings = () =>
    listMappings()
      .then((data) => {
        const list = Array.isArray(data) ? data : (data as { items?: Mapping[] })?.items || [];
        setMappings(list);
        if (!selected && list.length) setSelected(list[0].id);
      })
      .catch((e: Error) => setError(e.message));

  useEffect(() => {
    loadMappings();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selected) return;
    // 快速点不同对照表时旧 fetch 可能后到、覆盖新选中的 entries。
    let cancelled = false;
    listMappingEntries(selected, { page: 1, size: 200 })
      .then((d) => { if (!cancelled) setEntries(d); })
      .catch((e: Error) => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [selected]);

  const submit = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) {
      setError('请选择 xlsx 文件');
      return;
    }
    if (!name) {
      setError('请输入对照表名称');
      return;
    }
    const fd = new FormData();
    fd.append('file', file);
    fd.append('name', name);
    setUploading(true);
    try {
      await createMapping(fd);
      setName('');
      if (fileRef.current) fileRef.current.value = '';
      await loadMappings();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setUploading(false);
    }
  };

  const onDelete = async (id: number | string) => {
    if (!confirm('确认删除？仅在无批次引用时可删。')) return;
    try {
      await deleteMapping(id);
      if (selected === id) setSelected(null);
      loadMappings();
    } catch (e: any) {
      alert(e.message);
    }
  };

  const cur = mappings.find((m) => m.id === selected);

  return (
    <>
      <div className="toolbar">
        <span className="crumb">
          谐振器 <span style={{ color: 'var(--fg-4)' }}>›</span> <b>对照表</b>
        </span>
        <div className="spacer" />
        <input
          className="input sm"
          placeholder="新对照表名称（如 ELB003）"
          value={name}
          onChange={(e) => setName(e.target.value)}
          style={{ width: 220 }}
        />
        <input ref={fileRef} type="file" accept=".xlsx,.xls" />
        <button className="btn primary" onClick={submit} disabled={uploading}>
          <I.plus size={13} /> {uploading ? '上传中...' : '上传对照表'}
        </button>
      </div>
      {error && (
        <div style={{ padding: 12, background: 'var(--fail-soft)', color: 'var(--fail)' }}>{error}</div>
      )}
      <div className="workspace" style={{ gridTemplateColumns: '320px 1fr' }}>
        <div className="panel">
          <div className="panel-head">
            <I.table size={12} />
            <span>对照表 · {mappings.length}</span>
          </div>
          <div className="panel-body" style={{ padding: 0 }}>
            {mappings.length === 0 && (
              <div style={{ padding: 14, color: 'var(--fg-4)', fontSize: 12 }}>
                暂无对照表
              </div>
            )}
            {mappings.map((m) => (
              <div
                key={m.id}
                onClick={() => setSelected(m.id)}
                style={{
                  padding: '10px 14px',
                  borderBottom: '1px solid var(--border-soft)',
                  borderLeft:
                    selected === m.id ? '3px solid var(--primary)' : '3px solid transparent',
                  background: selected === m.id ? 'var(--primary-soft)' : 'transparent',
                  cursor: 'pointer',
                }}
              >
                <div className="row-flex" style={{ marginBottom: 4 }}>
                  <span style={{ fontWeight: 600, fontSize: 13, color: 'var(--fg-1)' }}>{m.name}</span>
                  <span className="chip" style={{ marginLeft: 'auto' }}>
                    {m.in_use_by_batches || 0} 批次
                  </span>
                </div>
                <div className="mono dim" style={{ fontSize: 11 }}>
                  {m.entry_count} 条目 · {m.uploaded_at ? new Date(m.uploaded_at).toLocaleDateString() : '—'}
                </div>
              </div>
            ))}
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div
            className="panel-head"
            style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-panel)' }}
          >
            <span style={{ fontWeight: 600, color: 'var(--fg-1)', textTransform: 'none', fontSize: 13, letterSpacing: 0 }}>
              {cur?.name || '—'}
            </span>
            <span className="dim mono" style={{ marginLeft: 8, fontSize: 11 }}>
              {entries.total || 0} 条目
            </span>
            {cur && (
              <button
                className="btn ghost sm danger"
                style={{ marginLeft: 'auto', height: 22 }}
                onClick={() => onDelete(cur.id)}
              >
                <I.trash size={12} /> 删除
              </button>
            )}
          </div>
          <div style={{ flex: 1, overflow: 'auto', background: 'var(--bg-panel)' }}>
            <table className="dtable">
              <thead>
                <tr>
                  <th>标记</th>
                  <th>描述</th>
                  <th className="num">电极间隙 (EG)</th>
                  <th className="num">指长 (FL)</th>
                  <th className="num">孔径 (AG)</th>
                  <th className="num">S11 面积 (μm²)</th>
                  <th className="num">S22 面积 (μm²)</th>
                  <th>含 PF</th>
                </tr>
              </thead>
              <tbody>
                {(entries.items || []).map((e) => (
                  <EntryRow key={e.mark} entry={e} />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </>
  );
}
