import React, { memo, useEffect, useMemo, useRef, useState } from 'react';
import { buildColumnModel, columnGridTemplate, columnModelWidth } from './columnModel';
import type { AnalysisDataset } from './workspace';

export const WORKSHEET_ROW_HEIGHT = 28;
const HEADER_HEIGHT = 28;
const OVERSCAN = 8;

export function visibleRowRange(scrollTop: number, viewportHeight: number, rowCount: number) {
  const start = Math.max(0, Math.floor(scrollTop / WORKSHEET_ROW_HEIGHT) - OVERSCAN);
  const end = Math.min(
    rowCount,
    Math.ceil((scrollTop + viewportHeight) / WORKSHEET_ROW_HEIGHT) + OVERSCAN,
  );
  return { start, end };
}

export function rowIndexAtPointer(
  clientY: number,
  rowAreaTop: number,
  rowCount: number,
) {
  if (rowCount === 0) return -1;
  const contentY = clientY - rowAreaTop;
  return Math.max(0, Math.min(rowCount - 1, Math.floor(contentY / WORKSHEET_ROW_HEIGHT)));
}

export function rowIdsInRange(dataset: AnalysisDataset, from: number, to: number) {
  const lo = Math.max(0, Math.min(from, to));
  const hi = Math.min(dataset.rows.length - 1, Math.max(from, to));
  return dataset.rows.slice(lo, hi + 1).map((row) => row.rowId);
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

type SelectionMode = 'replace' | 'toggle' | 'add';

interface WorksheetGridProps {
  dataset: AnalysisDataset;
  selectedRowIds: Set<string>;
  onSelection: (rowIds: string[], mode?: SelectionMode) => void;
  onEdit: (rowId: string, column: string, value: unknown) => void;
  onDelete?: () => void;
  onSelectAll?: () => void;
  onContextMenu?: (event: React.MouseEvent<HTMLDivElement>) => void;
}

interface DragState {
  pointerId: number;
  startIndex: number;
  currentIndex: number;
  mode: SelectionMode;
  baseSelection: Set<string>;
  clientY: number;
}

function draftSelection(dataset: AnalysisDataset, drag: DragState) {
  const range = rowIdsInRange(dataset, drag.startIndex, drag.currentIndex);
  if (drag.mode === 'replace') return new Set(range);
  const next = new Set(drag.baseSelection);
  for (const rowId of range) {
    if (drag.mode === 'toggle' && next.has(rowId)) next.delete(rowId);
    else next.add(rowId);
  }
  return next;
}

function WorksheetGridImpl({
  dataset, selectedRowIds, onSelection, onEdit, onDelete, onSelectAll, onContextMenu,
}: WorksheetGridProps) {
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const anchorIndexRef = useRef<number | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const autoScrollFrameRef = useRef(0);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(500);
  const [focusedIndex, setFocusedIndex] = useState<number | null>(null);
  const [draggedRowIds, setDraggedRowIds] = useState<Set<string> | null>(null);
  const [editing, setEditing] = useState<{ rowId: string; column: string } | null>(null);

  const columnModel = useMemo(() => buildColumnModel(dataset.columns), [dataset.columns]);
  const template = useMemo(() => columnGridTemplate(columnModel), [columnModel]);
  const contentWidth = useMemo(() => columnModelWidth(columnModel), [columnModel]);
  const dataColumns = columnModel.filter((column) => column.role === 'data');
  const effectiveSelection = draggedRowIds || selectedRowIds;
  const { start, end } = visibleRowRange(scrollTop, viewportHeight, dataset.rows.length);
  const visibleRows = dataset.rows.slice(start, end);

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

  useEffect(() => () => cancelAnimationFrame(autoScrollFrameRef.current), []);

  useEffect(() => {
    if (dataset.rows.length === 0) {
      anchorIndexRef.current = null;
      setFocusedIndex(null);
    } else if (focusedIndex != null && focusedIndex >= dataset.rows.length) {
      const lastIndex = dataset.rows.length - 1;
      anchorIndexRef.current = lastIndex;
      setFocusedIndex(lastIndex);
    }
  }, [dataset.rows.length, focusedIndex]);

  useEffect(() => {
    const viewport = viewportRef.current;
    const firstSelected = selectedRowIds.values().next().value;
    if (!viewport || !firstSelected || dragRef.current) return;
    const index = dataset.rows.findIndex((row) => row.rowId === firstSelected);
    const rowTop = index * WORKSHEET_ROW_HEIGHT;
    const rowBottom = rowTop + WORKSHEET_ROW_HEIGHT;
    if (rowTop < viewport.scrollTop
      || rowBottom > viewport.scrollTop + viewport.clientHeight - HEADER_HEIGHT) {
      viewport.scrollTop = Math.max(0, index * WORKSHEET_ROW_HEIGHT - viewport.clientHeight / 2);
    }
  }, [dataset.rows, selectedRowIds]);

  const setDragIndex = (index: number) => {
    const drag = dragRef.current;
    if (!drag || index < 0 || index === drag.currentIndex) return;
    drag.currentIndex = index;
    setFocusedIndex(index);
    setDraggedRowIds(draftSelection(dataset, drag));
  };

  const continueAutoScroll = (): void => {
    cancelAnimationFrame(autoScrollFrameRef.current);
    const drag = dragRef.current;
    const viewport = viewportRef.current;
    if (!drag || !viewport) return;
    const rect = viewport.getBoundingClientRect();
    let delta = 0;
    if (drag.clientY < rect.top + HEADER_HEIGHT + 10) delta = -WORKSHEET_ROW_HEIGHT;
    if (drag.clientY > rect.bottom - 10) delta = WORKSHEET_ROW_HEIGHT;
    if (!delta) return;
    const before = viewport.scrollTop;
    viewport.scrollTop = Math.max(0, viewport.scrollTop + delta);
    setScrollTop(viewport.scrollTop);
    const rowAreaTop = viewport.querySelector('.analysis-grid-spacer')
      ?.getBoundingClientRect().top ?? rect.top + HEADER_HEIGHT;
    setDragIndex(rowIndexAtPointer(drag.clientY, rowAreaTop, dataset.rows.length));
    if (viewport.scrollTop !== before) {
      autoScrollFrameRef.current = requestAnimationFrame(continueAutoScroll);
    }
  };

  const beginDrag = (index: number, event: React.PointerEvent) => {
    if (event.button !== 0 || (event.target as Element).closest('input')) return;
    event.preventDefault();
    const viewport = viewportRef.current;
    viewport?.focus();
    viewport?.setPointerCapture(event.pointerId);
    const startIndex = event.shiftKey && anchorIndexRef.current != null
      ? anchorIndexRef.current : index;
    const mode: SelectionMode = event.metaKey || event.ctrlKey ? 'toggle' : 'replace';
    const drag: DragState = {
      pointerId: event.pointerId,
      startIndex,
      currentIndex: index,
      mode,
      baseSelection: new Set(selectedRowIds),
      clientY: event.clientY,
    };
    dragRef.current = drag;
    if (!event.shiftKey) anchorIndexRef.current = index;
    setFocusedIndex(index);
    setDraggedRowIds(draftSelection(dataset, drag));
  };

  const moveDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    const viewport = viewportRef.current;
    if (!drag || drag.pointerId !== event.pointerId || !viewport) return;
    drag.clientY = event.clientY;
    const rowAreaTop = viewport.querySelector('.analysis-grid-spacer')
      ?.getBoundingClientRect().top ?? viewport.getBoundingClientRect().top + HEADER_HEIGHT;
    setDragIndex(rowIndexAtPointer(event.clientY, rowAreaTop, dataset.rows.length));
    continueAutoScroll();
  };

  const finishDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    cancelAnimationFrame(autoScrollFrameRef.current);
    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    onSelection(rowIdsInRange(dataset, drag.startIndex, drag.currentIndex), drag.mode);
    setDraggedRowIds(null);
  };

  const focusRow = (index: number, extend: boolean) => {
    if (dataset.rows.length === 0) return;
    const nextIndex = Math.max(0, Math.min(dataset.rows.length - 1, index));
    const anchor = extend && anchorIndexRef.current != null ? anchorIndexRef.current : nextIndex;
    if (!extend) anchorIndexRef.current = nextIndex;
    setFocusedIndex(nextIndex);
    onSelection(rowIdsInRange(dataset, anchor, nextIndex), 'replace');
    const viewport = viewportRef.current;
    if (!viewport) return;
    const rowTop = nextIndex * WORKSHEET_ROW_HEIGHT;
    const rowBottom = rowTop + WORKSHEET_ROW_HEIGHT;
    if (rowTop < viewport.scrollTop) viewport.scrollTop = rowTop;
    else if (rowBottom > viewport.scrollTop + viewport.clientHeight - HEADER_HEIGHT) {
      viewport.scrollTop = rowBottom - viewport.clientHeight + HEADER_HEIGHT;
    }
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (editing) return;
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'a') {
      event.preventDefault();
      event.stopPropagation();
      if (onSelectAll) {
        onSelectAll();
        return;
      }
      anchorIndexRef.current = dataset.rows.length ? 0 : null;
      setFocusedIndex(dataset.rows.length ? dataset.rows.length - 1 : null);
      onSelection(dataset.rows.map((row) => row.rowId), 'replace');
      return;
    }
    if (event.key === 'Delete' || event.key === 'Backspace') {
      if (!onDelete) return;
      event.preventDefault();
      event.stopPropagation();
      onDelete();
      return;
    }
    const current = focusedIndex
      ?? dataset.rows.findIndex((row) => selectedRowIds.has(row.rowId));
    const pageSize = Math.max(1, Math.floor((viewportHeight - HEADER_HEIGHT) / WORKSHEET_ROW_HEIGHT));
    let nextIndex: number | null = null;
    if (event.key === 'ArrowUp') nextIndex = Math.max(0, current < 0 ? 0 : current - 1);
    if (event.key === 'ArrowDown') {
      nextIndex = Math.min(dataset.rows.length - 1, current < 0 ? 0 : current + 1);
    }
    if (event.key === 'PageUp') nextIndex = Math.max(0, current < 0 ? 0 : current - pageSize);
    if (event.key === 'PageDown') {
      nextIndex = Math.min(dataset.rows.length - 1, current < 0 ? 0 : current + pageSize);
    }
    if (event.key === 'Home') nextIndex = 0;
    if (event.key === 'End') nextIndex = dataset.rows.length - 1;
    if (nextIndex == null || nextIndex < 0) return;
    event.preventDefault();
    event.stopPropagation();
    focusRow(nextIndex, event.shiftKey);
  };

  return (
    <div className="analysis-sheet" aria-label={dataset.name} onContextMenu={onContextMenu}>
      <div className="analysis-sheet-status">
        <span>{dataset.name}</span>
        <span>{dataset.rows.length.toLocaleString()} 行 · {dataColumns.length} 列</span>
        <span>{effectiveSelection.size.toLocaleString()} 行已选中</span>
        <span className="analysis-sheet-local">工作区副本</span>
      </div>
      <div
        ref={viewportRef}
        className="analysis-grid-viewport"
        tabIndex={0}
        onScroll={(event) => {
          setScrollTop(event.currentTarget.scrollTop);
          setViewportHeight(event.currentTarget.clientHeight);
        }}
        onPointerMove={moveDrag}
        onPointerUp={finishDrag}
        onPointerCancel={finishDrag}
        onKeyDown={handleKeyDown}
      >
        <div
          className="analysis-grid-header"
          style={{ gridTemplateColumns: template, width: contentWidth }}
        >
          {columnModel.map((column) => (
            <div
              className={`analysis-grid-cell ${column.role}`}
              data-column-key={column.key}
              key={column.key}
            >
              {column.label}
            </div>
          ))}
        </div>
        <div
          className="analysis-grid-spacer"
          style={{ height: dataset.rows.length * WORKSHEET_ROW_HEIGHT, width: contentWidth }}
        >
          {visibleRows.map((row, localIndex) => {
            const rowIndex = start + localIndex;
            const selected = effectiveSelection.has(row.rowId);
            return (
              <div
                key={row.rowId}
                className={`analysis-grid-row${selected ? ' selected' : ''}`}
                data-row-index={rowIndex}
                style={{
                  gridTemplateColumns: template,
                  width: contentWidth,
                  transform: `translateY(${rowIndex * WORKSHEET_ROW_HEIGHT}px)`,
                }}
                onPointerDown={(event) => beginDrag(rowIndex, event)}
              >
                <div className="analysis-grid-cell row-number" data-column-key="__rowNumber">
                  {rowIndex + 1}
                </div>
                <div className="analysis-grid-cell selection" data-column-key="__selection">
                  {selected ? '✓' : ''}
                </div>
                <div
                  className="analysis-grid-cell mono row-id"
                  data-column-key="rowId"
                  title={row.rowId}
                >
                  {row.rowId}
                </div>
                {dataColumns.map((column) => {
                  const active = editing?.rowId === row.rowId && editing.column === column.key;
                  return (
                    <div
                      className="analysis-grid-cell editable"
                      data-column-key={column.key}
                      key={column.key}
                      title={displayValue(row[column.key])}
                      onDoubleClick={(event) => {
                        event.stopPropagation();
                        setEditing({ rowId: row.rowId, column: column.key });
                      }}
                    >
                      {active ? (
                        <input
                          autoFocus
                          defaultValue={displayValue(row[column.key])}
                          onPointerDown={(event) => event.stopPropagation()}
                          onClick={(event) => event.stopPropagation()}
                          onBlur={(event) => {
                            onEdit(
                              row.rowId,
                              column.key,
                              coerceEditedValue(row[column.key], event.target.value),
                            );
                            setEditing(null);
                          }}
                          onKeyDown={(event) => {
                            event.stopPropagation();
                            if (event.key === 'Enter') event.currentTarget.blur();
                            if (event.key === 'Escape') setEditing(null);
                          }}
                        />
                      ) : displayValue(row[column.key])}
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
