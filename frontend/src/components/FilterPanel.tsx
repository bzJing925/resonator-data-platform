import React, { useEffect, useMemo, useRef, useState } from 'react';
import I from './Icons.jsx';
import useFields, { displayLabel } from '../hooks/useFields.js';
import { distinctValues } from '../api/endpoints.js';

/* -------------------------------------------------------------------------
 * 筛选协议（前端 → 后端）
 *
 * filters = { op: 'and' | 'or', children: [ { field, op, value }, ... ] }
 *
 * 只有一层（无嵌套）。空条件在序列化时丢掉；整棵树没条件时发 `{}`。
 * 后端 query.py 的 _build_node 已经支持这个格式。
 * ----------------------------------------------------------------------- */

const SECTION_LABELS = {
  categorical: '类别字段',
  process: '工艺字段',
  geometric: '几何字段',
  numeric: '数值字段',
};
const SECTION_ORDER = ['categorical', 'process', 'geometric', 'numeric'];
// 这两类按字符串语义处理（运算符菜单走 string ops）。
// process 段（eg/fl/ag/area_um2）底层是 float，但用户当枚举用，多选 IN 更自然。
const STRING_SECTIONS = new Set(['categorical', 'process']);

const NUMERIC_OPS = [
  { key: 'eq',       label: '=' },
  { key: 'neq',      label: '≠' },
  { key: 'gt',       label: '>' },
  { key: 'gte',      label: '≥' },
  { key: 'lt',       label: '<' },
  { key: 'lte',      label: '≤' },
  { key: 'between',  label: '区间' },
  { key: 'in',       label: '属于' },
  { key: 'is_null',  label: '为空' },
  { key: 'not_null', label: '非空' },
];

const STRING_OPS = [
  { key: 'in',       label: '属于' },
  { key: 'eq',       label: '=' },
  { key: 'neq',      label: '≠' },
  { key: 'contains', label: '包含' },
  { key: 'like',     label: '模糊匹配' },
  { key: 'is_null',  label: '为空' },
  { key: 'not_null', label: '非空' },
];

const NO_VALUE_OPS = new Set(['is_null', 'not_null']);
const LIST_OPS = new Set(['in']);
const RANGE_OPS = new Set(['between']);

function uid() {
  return Math.random().toString(36).slice(2, 10);
}
function makeCondition() {
  return { id: uid(), field: '', op: 'eq', value: '' };
}
function opsFor(fieldMeta) {
  if (!fieldMeta) return STRING_OPS;
  return STRING_SECTIONS.has(fieldMeta.section) ? STRING_OPS : NUMERIC_OPS;
}
function defaultOpFor(fieldMeta) {
  if (!fieldMeta) return 'eq';
  return STRING_SECTIONS.has(fieldMeta.section) ? 'in' : 'eq';
}
function reconcileOp(op, fieldMeta) {
  const ops = opsFor(fieldMeta);
  return ops.some((o) => o.key === op) ? op : ops[0].key;
}

function serialize(groupOp, conditions) {
  const kids = conditions.map((c) => {
    if (!c.field) return null;
    if (NO_VALUE_OPS.has(c.op)) return { field: c.field, op: c.op };
    if (RANGE_OPS.has(c.op)) {
      const v = Array.isArray(c.value) ? c.value : [];
      const lo = parseFloat(v[0]);
      const hi = parseFloat(v[1]);
      if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null;
      return { field: c.field, op: 'between', value: [lo, hi] };
    }
    if (LIST_OPS.has(c.op)) {
      const items = Array.isArray(c.value)
        ? c.value
        : String(c.value ?? '').split(',').map((s) => s.trim()).filter(Boolean);
      if (items.length === 0) return null;
      return { field: c.field, op: 'in', value: items };
    }
    if (c.value === '' || c.value == null) return null;
    return { field: c.field, op: c.op, value: c.value };
  }).filter(Boolean);
  if (kids.length === 0) return {};
  return { op: groupOp, children: kids };
}

/* -------------------------------------------------------------------------
 * FieldSelect — 字段下拉，按段分组，全列可选。
 * ----------------------------------------------------------------------- */
function FieldSelect({ fields, value, onChange }) {
  return (
    <select
      className="input mono filter-field-sel"
      value={value || ''}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">选择列…</option>
      {SECTION_ORDER.map((section) => {
        const items = fields?.raw?.[section] || [];
        if (items.length === 0) return null;
        return (
          <optgroup key={section} label={SECTION_LABELS[section] || section}>
            {items.map((f) => (
              <option key={f.name} value={f.name}>{displayLabel(f)}</option>
            ))}
          </optgroup>
        );
      })}
    </select>
  );
}

/* -------------------------------------------------------------------------
 * MultiSelect — 一个紧凑下拉，按钮显示「已选 N 项 ▾」，点开后内联展示可选值。
 * 用于 categorical / process 字段 + op='in' 的场景；值通过 fetcher 或 options
 * 提供。Esc / 点外面关闭。
 * ----------------------------------------------------------------------- */
function MultiSelect({ fieldMeta, value, onChange }) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState(null);
  const [error, setError] = useState(null);
  const popRef = useRef(null);
  const btnRef = useRef(null);

  const selected = useMemo(() => new Set((value || []).map(String)), [value]);

  // 懒加载：第一次展开时拉取一次 distinct values。
  useEffect(() => {
    if (!open || items !== null) return;
    if (fieldMeta?.values) {
      setItems(fieldMeta.values.slice());
      return;
    }
    if (!fieldMeta?.values_endpoint) {
      setItems([]); // 既无静态也无远程候选 → 留给用户手工输入路径
      return;
    }
    distinctValues(fieldMeta.name)
      .then((res) => setItems(Array.isArray(res) ? res : res.values || []))
      .catch((e) => setError(e.message || String(e)));
  }, [open, items, fieldMeta]);

  // 字段切换后清缓存。
  useEffect(() => { setItems(null); setError(null); }, [fieldMeta?.name]);

  // 点击外面关闭。
  useEffect(() => {
    if (!open) return;
    const onDoc = (e) => {
      if (popRef.current?.contains(e.target)) return;
      if (btnRef.current?.contains(e.target)) return;
      setOpen(false);
    };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const toggle = (v) => {
    const sv = String(v);
    const next = (items || []).filter((it) => {
      const isSel = selected.has(String(it));
      if (String(it) === sv) return !isSel;
      return isSel;
    });
    onChange(next);
  };
  const selectAll = () => onChange((items || []).slice());
  const selectNone = () => onChange([]);

  // 按钮上的摘要：B001, B002 / 已选 5 项 / 全部
  const summary = useMemo(() => {
    if (!value || value.length === 0) return '未选';
    if (value.length <= 2) return value.map(String).join(', ');
    return `已选 ${value.length} 项`;
  }, [value]);

  return (
    <span className="filter-multiselect">
      <button
        ref={btnRef}
        type="button"
        className={`input mono filter-multiselect-btn${value && value.length ? ' active' : ''}`}
        onClick={() => setOpen((o) => !o)}
        title={value && value.length ? value.map(String).join(', ') : '点击选择'}
      >
        <span className="filter-multiselect-summary">{summary}</span>
        <span className="filter-multiselect-caret">▾</span>
      </button>
      {open && (
        <div ref={popRef} className="filter-multiselect-pop">
          {error && (
            <div className="dim" style={{ color: 'var(--fail)', fontSize: 11, padding: 6 }}>
              {error}
            </div>
          )}
          {items === null && !error && (
            <div className="dim" style={{ fontSize: 11, padding: 6 }}>加载中…</div>
          )}
          {items && items.length === 0 && !error && (
            <div className="dim" style={{ fontSize: 11, padding: 6 }}>无可用选项</div>
          )}
          {items && items.length > 0 && (
            <>
              <div className="filter-multiselect-actions">
                <button type="button" className="btn ghost sm" onClick={selectAll}>全选</button>
                <button type="button" className="btn ghost sm" onClick={selectNone}>清空</button>
              </div>
              <div className="filter-multiselect-list">
                {items.map((v) => {
                  const sv = String(v);
                  const checked = selected.has(sv);
                  return (
                    <label key={sv} className="filter-multiselect-item">
                      <span className={`cb${checked ? ' checked' : ''}`} aria-hidden>
                        {checked && <I.check size={9} stroke="#fff" sw={2.5} />}
                      </span>
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggle(v)}
                        style={{ position: 'absolute', opacity: 0, pointerEvents: 'none' }}
                      />
                      <span>{String(v)}</span>
                    </label>
                  );
                })}
              </div>
            </>
          )}
        </div>
      )}
    </span>
  );
}

/* -------------------------------------------------------------------------
 * ValueInput — 根据 (fieldMeta, op) 决定值控件。
 * ----------------------------------------------------------------------- */
function ValueInput({ fieldMeta, op, value, onChange }) {
  if (NO_VALUE_OPS.has(op)) {
    return <span className="filter-value-placeholder">（不需要值）</span>;
  }
  const isNumeric = fieldMeta && !STRING_SECTIONS.has(fieldMeta.section);

  if (RANGE_OPS.has(op)) {
    const v = Array.isArray(value) ? value : ['', ''];
    return (
      <span className="filter-value-range">
        <input
          className="input mono"
          placeholder="lo"
          value={v[0] ?? ''}
          onChange={(e) => onChange([e.target.value, v[1] ?? ''])}
        />
        <span className="dim" style={{ padding: '0 4px' }}>—</span>
        <input
          className="input mono"
          placeholder="hi"
          value={v[1] ?? ''}
          onChange={(e) => onChange([v[0] ?? '', e.target.value])}
        />
      </span>
    );
  }

  if (LIST_OPS.has(op)) {
    const hasKnown = !!(fieldMeta?.values || fieldMeta?.values_endpoint);
    if (hasKnown) {
      return (
        <MultiSelect
          fieldMeta={fieldMeta}
          value={Array.isArray(value) ? value : []}
          onChange={onChange}
        />
      );
    }
    // 未知 distinct → 退化成逗号分隔文本框
    return (
      <input
        className="input mono"
        placeholder="v1, v2, v3"
        value={Array.isArray(value) ? value.join(', ') : value ?? ''}
        onChange={(e) => onChange(e.target.value)}
        style={{ flex: 1, minWidth: 0 }}
      />
    );
  }

  // 标量
  return (
    <input
      className="input mono"
      type={isNumeric ? 'number' : 'text'}
      placeholder={isNumeric ? '数值' : '字符串'}
      value={value ?? ''}
      onChange={(e) => {
        const s = e.target.value;
        if (isNumeric) {
          if (s === '') onChange('');
          else {
            const n = parseFloat(s);
            onChange(Number.isFinite(n) ? n : s);
          }
        } else {
          onChange(s);
        }
      }}
      style={{ flex: 1, minWidth: 0 }}
    />
  );
}

/* -------------------------------------------------------------------------
 * ConditionRow — 一行筛选条件：字段 / 运算符 / 值 / 删除。
 * ----------------------------------------------------------------------- */
function ConditionRow({ cond, fields, onChange, onRemove }) {
  const fieldMeta = fields?.byName?.[cond.field] || null;
  const ops = opsFor(fieldMeta);

  const setField = (name) => {
    const meta = fields?.byName?.[name] || null;
    // 切换字段时，让 op 重置为该类型的默认值；类别字段默认 in 更顺手。
    onChange({ ...cond, field: name, op: defaultOpFor(meta), value: '' });
  };
  const setOp = (nextOp) => {
    // 值结构在 between / list / 标量 / 空值之间不通用，切换时清空。
    const wasRange = RANGE_OPS.has(cond.op);
    const isRange = RANGE_OPS.has(nextOp);
    const wasList = LIST_OPS.has(cond.op);
    const isList = LIST_OPS.has(nextOp);
    const wasNoVal = NO_VALUE_OPS.has(cond.op);
    const isNoVal = NO_VALUE_OPS.has(nextOp);
    let nextValue = cond.value;
    if (wasRange !== isRange || wasList !== isList || wasNoVal !== isNoVal) {
      nextValue = isRange ? ['', ''] : isList ? [] : '';
    }
    onChange({ ...cond, op: nextOp, value: nextValue });
  };

  return (
    <div className="filter-cond">
      <FieldSelect fields={fields} value={cond.field} onChange={setField} />
      <select
        className="input mono filter-op-sel"
        value={cond.op}
        onChange={(e) => setOp(e.target.value)}
        disabled={!cond.field}
      >
        {ops.map((o) => (
          <option key={o.key} value={o.key}>{o.label}</option>
        ))}
      </select>
      <ValueInput
        fieldMeta={fieldMeta}
        op={cond.op}
        value={cond.value}
        onChange={(v) => onChange({ ...cond, value: v })}
      />
      <button
        type="button"
        className="btn ghost sm filter-icon-btn"
        onClick={onRemove}
        title="删除该条件"
      >
        <I.trash size={12} />
      </button>
    </div>
  );
}

/* -------------------------------------------------------------------------
 * FilterPanel — 顶级面板。
 *
 * value:    后端期望的 filters dict（应用过的那一份），仅作初始展示参考。
 * onApply:  点"应用筛选"时回调一个 dict（`{}` 表示无筛选）。
 * ----------------------------------------------------------------------- */
export default function FilterPanel({ value, onApply }) {
  const { data: fields, loading: fLoading, error: fErr } = useFields();
  const [groupOp, setGroupOp] = useState('and');
  const [conditions, setConditions] = useState([makeCondition()]);

  const wire = useMemo(() => serialize(groupOp, conditions), [groupOp, conditions]);
  const hasAny = !!wire.children?.length;

  const apply = () => onApply(wire);
  const clear = () => {
    setConditions([makeCondition()]);
    setGroupOp('and');
    onApply({});
  };
  const addCondition = () =>
    setConditions((cs) => [...cs, makeCondition()]);
  const updateAt = (id, next) =>
    setConditions((cs) => cs.map((c) => (c.id === id ? next : c)));
  const removeAt = (id) =>
    setConditions((cs) => (cs.length <= 1 ? [makeCondition()] : cs.filter((c) => c.id !== id)));

  return (
    <div className="panel">
      <div className="panel-head">
        <I.filter size={12} />
        <span>筛选</span>
        <div className="filter-andor" style={{ marginLeft: 'auto' }}>
          <button
            type="button"
            className={`filter-andor-btn${groupOp === 'and' ? ' active' : ''}`}
            onClick={() => setGroupOp('and')}
            title="全部条件都要满足"
          >
            全部满足
          </button>
          <button
            type="button"
            className={`filter-andor-btn${groupOp === 'or' ? ' active' : ''}`}
            onClick={() => setGroupOp('or')}
            title="任一条件满足即可"
          >
            任一满足
          </button>
        </div>
      </div>
      <div className="panel-body">
        {fErr && (
          <div className="dim" style={{ color: 'var(--fail)', fontSize: 11 }}>
            字段加载失败：{fErr.message}
          </div>
        )}
        {fLoading && !fields && (
          <div className="dim" style={{ fontSize: 11 }}>加载字段中…</div>
        )}
        {fields && (
          <div className="filter-list">
            {conditions.map((c) => (
              <ConditionRow
                key={c.id}
                cond={c}
                fields={fields}
                onChange={(next) => updateAt(c.id, next)}
                onRemove={() => removeAt(c.id)}
              />
            ))}
          </div>
        )}
        <button
          type="button"
          className="btn ghost sm filter-add-btn"
          onClick={addCondition}
          disabled={fLoading}
        >
          <I.plus size={12} /> 添加筛选
        </button>
        <div className="hr" />
        <div style={{ display: 'flex', gap: 6 }}>
          <button
            className="btn primary"
            style={{ flex: 1, justifyContent: 'center' }}
            onClick={apply}
            disabled={fLoading}
          >
            应用筛选
          </button>
          <button
            className="btn"
            onClick={clear}
            disabled={fLoading}
          >
            清空
          </button>
        </div>
        {hasAny && (
          <div className="dim mono" style={{ fontSize: 10.5, marginTop: 8, wordBreak: 'break-all' }}>
            {JSON.stringify(wire)}
          </div>
        )}
      </div>
    </div>
  );
}
