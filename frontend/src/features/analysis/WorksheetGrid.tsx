import React, { memo, useEffect, useMemo, useRef, useState } from 'react';
import type { AnalysisDataset } from './workspace';

const ROW_HEIGHT = 28;
const OVERSCAN = 8;

export function visibleRowRange(scrollTop: number, viewportHeight: number, rowCount: number) {
  const start = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
  const end = Math.min(rowCount, Math.ceil((scrollTop + viewportHeight) / ROW_HEIGHT) + OVERSCAN);
  return { start, end };
}

function displayValue(value: unknown) {
  if (value == null) return '';
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : '';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function coerceEditedValue(previous: unknown, value: string) {
  if (typeof previous === 'number') {
    const parsed = Number(value);
    return value.trim() !== '' && Number.isFinite(parsed) ? parsed : previous;
  }
  if (typeof previous === 'boolean') return value === 'true';
  return value;
}

interface WorksheetGridProps {
  dataset: AnalysisDataset;
  selectedRowIds: Set<string>;
  onSelection: (rowIds: string[], mode?: 'replace' | 'toggle' | 'add') => void;
  onEdit: (rowId: string, column: string, value: unknown) => void;
}

function WorksheetGridImpl({ dataset, selectedRowIds, onSelection, onEdit }: WorksheetGridProps) {
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const anchorIndexRef = useRef<number | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(500);
  const [editing, setEditing] = useState<{ rowId: string; column: string } | null>(null);

  const columns = useMemo(() => {
    const keys = new Set<string>(['structureType']);
    for (const row of dataset.rows.slice(0, 200)) {
      Object.keys(row).forEach((key) => { if (key !== '__rowId') keys.add(key); });
    }
    return [...keys];
  }, [dataset.rows]);

  const { start, end } = visibleRowRange(scrollTop, viewportHeight, dataset.rows.length);
  const visibleRows = dataset.rows.slice(start, end);
  const template = `44px 58px repeat(${columns.length}, minmax(120px, 1fr))`;

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const updateHeight = () => setViewportHeight(viewport.clientHeight || 500);
    updateHeight();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(updateHeight);
    observer.observe(viewport);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const viewport = viewportRef.current;
    const firstSelected = selectedRowIds.values().next().value;
    if (!viewport || !firstSelected) return;
    const index = dataset.rows.findIndex((row) => row.__rowId === firstSelected);
    if (index < start || index >= end) {
      viewport.scrollTop = Math.max(0, index * ROW_HEIGHT - viewport.clientHeight / 2);
    }
  }, [dataset.rows, end, selectedRowIds, start]);

  const selectRow = (index: number, event: React.MouseEvent) => {
    const row = dataset.rows[index];
    if (!row) return;
    if (event.shiftKey && anchorIndexRef.current != null) {
      const lo = Math.min(anchorIndexRef.current, index);
      const hi = Math.max(anchorIndexRef.current, index);
      onSelection(dataset.rows.slice(lo, hi + 1).map((item) => item.__rowId), 'replace');
    } else {
      onSelection([row.__rowId], event.metaKey || event.ctrlKey ? 'toggle' : 'replace');
      anchorIndexRef.current = index;
    }
  };

  return (
    <div className="analysis-sheet" aria-label={dataset.name}>
      <div className="analysis-sheet-status">
        <span>{dataset.name}</span>
        <span>{dataset.rows.length.toLocaleString()} 行 · {columns.length} 列</span>
        <span>{selectedRowIds.size.toLocaleString()} 行已选中</span>
        <span className="analysis-sheet-local">工作区副本</span>
      </div>
      <div
        ref={viewportRef}
        className="analysis-grid-viewport"
        tabIndex={0}
        onMouseDown={(event) => event.currentTarget.focus()}
        onScroll={(event) => {
          setScrollTop(event.currentTarget.scrollTop);
          setViewportHeight(event.currentTarget.clientHeight);
        }}
      >
        <div className="analysis-grid-header" style={{ gridTemplateColumns: template }}>
          <div className="analysis-grid-cell corner" />
          <div className="analysis-grid-cell">rowId</div>
          {columns.map((column) => <div className="analysis-grid-cell" key={column}>{column}</div>)}
        </div>
        <div className="analysis-grid-spacer" style={{ height: dataset.rows.length * ROW_HEIGHT }}>
          {visibleRows.map((row, localIndex) => {
            const rowIndex = start + localIndex;
            const selected = selectedRowIds.has(row.__rowId);
            return (
              <div
                key={row.__rowId}
                className={`analysis-grid-row${selected ? ' selected' : ''}`}
                style={{ gridTemplateColumns: template, transform: `translateY(${rowIndex * ROW_HEIGHT}px)` }}
                onClick={(event) => selectRow(rowIndex, event)}
              >
                <div className="analysis-grid-cell row-number">{rowIndex + 1}</div>
                <div className="analysis-grid-cell mono" title={row.__rowId}>{row.__rowId}</div>
                {columns.map((column) => {
                  const active = editing?.rowId === row.__rowId && editing.column === column;
                  return (
                    <div
                      className="analysis-grid-cell editable"
                      key={column}
                      title={displayValue(row[column])}
                      onDoubleClick={(event) => {
                        event.stopPropagation();
                        setEditing({ rowId: row.__rowId, column });
                      }}
                    >
                      {active ? (
                        <input
                          autoFocus
                          defaultValue={displayValue(row[column])}
                          onClick={(event) => event.stopPropagation()}
                          onBlur={(event) => {
                            onEdit(row.__rowId, column, coerceEditedValue(row[column], event.target.value));
                            setEditing(null);
                          }}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter') event.currentTarget.blur();
                            if (event.key === 'Escape') setEditing(null);
                          }}
                        />
                      ) : displayValue(row[column])}
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export default memo(WorksheetGridImpl);
