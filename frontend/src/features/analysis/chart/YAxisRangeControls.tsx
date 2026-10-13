import React, { useEffect, useState } from 'react';
import type { ChartField } from '../../../types';
import type { YAxisRanges } from './chartInteraction';

function MetricRange({ field, range, onApply }: {
  field: ChartField;
  range?: [number, number];
  onApply: (range: [number, number]) => void;
}) {
  const [min, setMin] = useState('');
  const [max, setMax] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    setMin(range ? String(range[0]) : '');
    setMax(range ? String(range[1]) : '');
    setError('');
  }, [range?.[0], range?.[1]]);
  const label = field.label || field.name;
  return <form className="analysis-y-range" onSubmit={(event) => {
    event.preventDefault();
    const low = Number(min), high = Number(max);
    if (!min.trim() || !max.trim() || !Number.isFinite(low) || !Number.isFinite(high) || low >= high) {
      setError('请输入有效数值，最小值须小于最大值');
      return;
    }
    setError('');
    onApply([low, high]);
  }}>
    <div className="field-label">{label}{field.unit ? ` (${field.unit})` : ''}</div>
    <div className="analysis-y-range-inputs">
      <input className="input mono" aria-label={`${label} Y轴最小值`} type="number" step="any"
        value={min} onChange={(event) => setMin(event.target.value)} />
      <span>～</span>
      <input className="input mono" aria-label={`${label} Y轴最大值`} type="number" step="any"
        value={max} onChange={(event) => setMax(event.target.value)} />
      <button className="btn sm" type="submit" aria-label={`应用${label} Y轴范围`}>应用</button>
    </div>
    {error && <div role="alert" className="hint">{error}</div>}
  </form>;
}

export default function YAxisRangeControls({ fields, ranges, onApply, onReset }: {
  fields: ChartField[];
  ranges: YAxisRanges;
  onApply: (metric: string, range: [number, number]) => void;
  onReset: () => void;
}) {
  return <section className="section" aria-label="Y轴范围">
    <div className="section-title">Y轴范围</div>
    <div className="hint">自动范围：数据最小值 − 5 ～ 最大值 + 5</div>
    {fields.filter((field) => !field.isCategorical).map((field) =>
      <MetricRange key={field.name} field={field} range={ranges[field.name]}
        onApply={(range) => onApply(field.name, range)} />)}
    <button className="btn sm" onClick={onReset}>恢复自动Y轴范围</button>
  </section>;
}
