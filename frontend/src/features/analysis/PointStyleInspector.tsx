import React, { useEffect, useMemo, useState } from 'react';
import type { ChartField } from '../../types';
import type { AnalysisDataset, RowId } from './model/dataset';
import type { PointStyleOverride, PointStylePatch } from './model/commands';
import {
  pointStyleForTarget,
  RESET_POINT_STYLE,
  resolvePointStyleTarget,
  type PointStyleScope,
} from './pointStyle';

const DEFAULT_STYLE: Required<PointStyleOverride> = {
  color: '#5fa8d3',
  size: 6,
  symbol: 'circle',
  opacity: 0.82,
  outlineColor: '#ffffff',
  outlineWidth: 0.5,
};

const SYMBOLS = [
  ['circle', '圆形'],
  ['square', '方形'],
  ['diamond', '菱形'],
  ['triangle-up', '上三角'],
  ['triangle-down', '下三角'],
  ['cross', '十字'],
  ['x', '叉形'],
] as const;

interface PointStyleInspectorProps {
  dataset: AnalysisDataset;
  selectedRowIds: Set<RowId>;
  anchorRowId: RowId | null;
  metrics: ChartField[];
  styles?: Record<string, PointStyleOverride>;
  onUpdate: (rowIds: RowId[], patch: PointStylePatch, metric?: string) => void;
}

export default function PointStyleInspector({
  dataset,
  selectedRowIds,
  anchorRowId,
  metrics,
  styles,
  onUpdate,
}: PointStyleInspectorProps) {
  const [scope, setScope] = useState<PointStyleScope>('selected');
  const [metric, setMetric] = useState(metrics[0]?.name || '');
  const [draft, setDraft] = useState(DEFAULT_STYLE);

  useEffect(() => {
    if (!metrics.some((candidate) => candidate.name === metric)) {
      setMetric(metrics[0]?.name || '');
    }
  }, [metric, metrics]);

  const target = useMemo(() => resolvePointStyleTarget({
    dataset,
    selectedRowIds,
    anchorRowId,
    scope,
    metric,
  }), [anchorRowId, dataset, metric, scope, selectedRowIds]);
  const storedStyle = pointStyleForTarget(styles, target);
  const storedStyleKey = JSON.stringify(storedStyle);

  useEffect(() => {
    setDraft({ ...DEFAULT_STYLE, ...storedStyle });
    // The serialized key intentionally makes Undo/Redo refresh the controls.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, metric, target.rowIds.join('\u0000'), target.metric, storedStyleKey]);

  const canApply = target.metric ? !!target.metric : target.rowIds.length > 0;
  const apply = <Key extends keyof PointStyleOverride>(
    key: Key,
    value: PointStyleOverride[Key],
  ) => {
    if (!canApply) return;
    setDraft((current) => ({ ...current, [key]: value }));
    onUpdate(target.rowIds, { [key]: value }, target.metric);
  };

  return (
    <section className="point-style-inspector" aria-labelledby="point-style-title">
      <h2 id="point-style-title">数据点样式</h2>
      <p className="point-style-summary">
        {target.metric ? `指标 ${target.metric}` : `${target.rowIds.length} 个数据点`}
      </p>

      <label className="point-style-field">
        <span>作用范围</span>
        <select
          className="select"
          data-testid="point-style-scope"
          value={scope}
          onChange={(event) => setScope(event.target.value as PointStyleScope)}
        >
          <option value="selected">选中点</option>
          <option value="group">当前分组</option>
          <option value="series">当前 Series</option>
          <option value="metric">当前指标</option>
        </select>
      </label>

      <label className="point-style-field">
        <span>当前指标</span>
        <select
          className="select"
          data-testid="point-style-metric"
          value={metric}
          onChange={(event) => setMetric(event.target.value)}
        >
          {metrics.map((candidate) => (
            <option key={candidate.name} value={candidate.name}>
              {candidate.label || candidate.name}
            </option>
          ))}
        </select>
      </label>

      <div className="point-style-grid">
        <label className="point-style-field compact">
          <span>填充颜色</span>
          <input
            aria-label="填充颜色"
            data-testid="point-style-color"
            type="color"
            value={draft.color}
            disabled={!canApply}
            onChange={(event) => apply('color', event.target.value)}
          />
        </label>
        <label className="point-style-field compact">
          <span>点大小</span>
          <input
            className="input mono"
            aria-label="点大小"
            data-testid="point-style-size"
            type="number"
            min={1}
            max={40}
            step={1}
            value={draft.size}
            disabled={!canApply}
            onChange={(event) => apply('size', Math.min(40, Math.max(1, Number(event.target.value))))}
          />
        </label>
        <label className="point-style-field wide">
          <span>形状</span>
          <select
            className="select"
            aria-label="形状"
            data-testid="point-style-symbol"
            value={draft.symbol}
            disabled={!canApply}
            onChange={(event) => apply('symbol', event.target.value)}
          >
            {SYMBOLS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <label className="point-style-field compact">
          <span>透明度</span>
          <input
            className="input mono"
            aria-label="透明度"
            data-testid="point-style-opacity"
            type="number"
            min={0.05}
            max={1}
            step={0.05}
            value={draft.opacity}
            disabled={!canApply}
            onChange={(event) => apply('opacity', Math.min(1, Math.max(0.05, Number(event.target.value))))}
          />
        </label>
        <label className="point-style-field compact">
          <span>描边颜色</span>
          <input
            aria-label="描边颜色"
            data-testid="point-style-outline-color"
            type="color"
            value={draft.outlineColor}
            disabled={!canApply}
            onChange={(event) => apply('outlineColor', event.target.value)}
          />
        </label>
        <label className="point-style-field compact">
          <span>描边宽度</span>
          <input
            className="input mono"
            aria-label="描边宽度"
            data-testid="point-style-outline-width"
            type="number"
            min={0}
            max={8}
            step={0.5}
            value={draft.outlineWidth}
            disabled={!canApply}
            onChange={(event) => apply('outlineWidth', Math.min(8, Math.max(0, Number(event.target.value))))}
          />
        </label>
      </div>

      {!canApply && <p className="point-style-empty">请先在图表或数据表中选择数据点。</p>}
      <button
        type="button"
        className="btn point-style-reset"
        disabled={!canApply}
        onClick={() => onUpdate(target.rowIds, RESET_POINT_STYLE, target.metric)}
      >
        重置样式
      </button>
    </section>
  );
}
