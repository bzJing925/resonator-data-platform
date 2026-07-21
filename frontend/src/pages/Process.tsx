import { useEffect, useMemo, useState } from 'react';
import I from '../components/Icons';
import { getProcessStep, listProcessSteps } from '../api/endpoints';
import type { ProcessFieldMeta, ProcessStep, ProcessStepData } from '../types';
import {
  applyFilters,
  fmtValue,
  sortRows,
  toCsv,
  type Filters,
  type Row,
  type SortDir,
} from '../utils/processTable';

function downloadText(filename: string, text: string) {
  const blob = new Blob([text], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export default function Process() {
  const [steps, setSteps] = useState<ProcessStep[]>([]);
  const [active, setActive] = useState<string>('');
  const [data, setData] = useState<ProcessStepData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>({});
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>('asc');
  const [showFilters, setShowFilters] = useState(false);

  useEffect(() => {
    listProcessSteps()
      .then((s) => {
        setSteps(s);
        if (s.length > 0) setActive((cur) => cur || s[0].key);
      })
      .catch((e) => setError(`加载工序列表失败: ${e?.message ?? e}`));
  }, []);

  useEffect(() => {
    if (!active) return;
    setLoading(true);
    setError(null);
    setFilters({});
    setSortKey(null);
    setSortDir('asc');
    getProcessStep(active)
      .then(setData)
      .catch((e) => setError(`加载工艺数据失败: ${e?.response?.data?.detail ?? e?.message ?? e}`))
      .finally(() => setLoading(false));
  }, [active]);

  const fields: ProcessFieldMeta[] = data?.fields ?? [];
  const rows: Row[] = useMemo(() => data?.rows ?? [], [data]);
  const viewRows = useMemo(
    () => sortRows(applyFilters(rows, fields, filters), sortKey, sortDir),
    [rows, fields, filters, sortKey, sortDir],
  );

  const toggleSort = (name: string) => {
    if (sortKey !== name) {
      setSortKey(name);
      setSortDir('asc');
    } else if (sortDir === 'asc') {
      setSortDir('desc');
    } else {
      setSortKey(null);
    }
  };

  const setFilter = (name: string, patch: { min?: string; max?: string; text?: string }) =>
    setFilters((f) => ({ ...f, [name]: { ...f[name], ...patch } }));

  return (
    <div
      className="page"
      style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}
    >
      <div
        className="page-head"
        style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 16px' }}
      >
        <span className="page-title" style={{ fontSize: 15, fontWeight: 600 }}>
          工艺数据
        </span>
        <div style={{ display: 'flex', gap: 4, marginLeft: 16 }}>
          {steps.map((s) => (
            <button
              key={s.key}
              className={`btn ghost sm${active === s.key ? ' primary' : ''}`}
              onClick={() => setActive(s.key)}
            >
              {s.label} ({s.row_count})
            </button>
          ))}
        </div>
        <div style={{ flex: 1 }} />
        <button className="btn ghost sm" title="筛选" onClick={() => setShowFilters((v) => !v)}>
          <I.filter size={14} /> 筛选
        </button>
        <button
          className="btn ghost sm"
          title="导出当前筛选结果为 CSV"
          onClick={() => downloadText(`process_${active}.csv`, toCsv(viewRows, fields))}
          disabled={viewRows.length === 0}
        >
          <I.download size={14} /> 导出 CSV
        </button>
      </div>

      {error && (
        <div style={{ padding: '8px 16px', color: 'var(--danger, #e5534b)' }}>{error}</div>
      )}

      {showFilters && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
            gap: 8,
            padding: '8px 16px',
            borderTop: '1px solid var(--line, #2a2f3a)',
          }}
        >
          {fields.map((f) =>
            f.kind === 'numeric' ? (
              <div key={f.name} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                <span style={{ fontSize: 12, color: 'var(--fg-3)', minWidth: 56 }}>{f.label}</span>
                <input
                  style={{ width: 52 }}
                  placeholder="最小"
                  value={filters[f.name]?.min ?? ''}
                  onChange={(e) => setFilter(f.name, { min: e.target.value })}
                />
                <span style={{ color: 'var(--fg-3)' }}>~</span>
                <input
                  style={{ width: 52 }}
                  placeholder="最大"
                  value={filters[f.name]?.max ?? ''}
                  onChange={(e) => setFilter(f.name, { max: e.target.value })}
                />
              </div>
            ) : (
              <div key={f.name} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                <span style={{ fontSize: 12, color: 'var(--fg-3)', minWidth: 56 }}>{f.label}</span>
                <input
                  style={{ flex: 1 }}
                  placeholder={`${f.label} 包含`}
                  value={filters[f.name]?.text ?? ''}
                  onChange={(e) => setFilter(f.name, { text: e.target.value })}
                />
              </div>
            ),
          )}
        </div>
      )}

      <div style={{ padding: '4px 16px', fontSize: 12, color: 'var(--fg-3)' }}>
        共 {rows.length} 行{viewRows.length !== rows.length ? `，筛选后 ${viewRows.length} 行` : ''}
        {loading ? '，加载中…' : ''}
      </div>

      <div style={{ flex: 1, overflow: 'auto', padding: '0 16px 16px' }}>
        <table className="dtable">
          <thead>
            <tr>
              {fields.map((f) => (
                <th
                  key={f.name}
                  onClick={() => toggleSort(f.name)}
                  style={{ cursor: 'pointer', whiteSpace: 'nowrap' }}
                  title="点击排序"
                >
                  {f.label}
                  {f.unit ? ` (${f.unit})` : ''}
                  {sortKey === f.name ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {viewRows.map((row, i) => (
              <tr key={i}>
                {fields.map((f) => (
                  <td key={f.name} className={f.kind === 'numeric' ? 'num' : undefined}>
                    {fmtValue(row[f.name])}
                  </td>
                ))}
              </tr>
            ))}
            {!loading && viewRows.length === 0 && (
              <tr>
                <td colSpan={fields.length} style={{ textAlign: 'center', color: 'var(--fg-3)' }}>
                  暂无数据
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
