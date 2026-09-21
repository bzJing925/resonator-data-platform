import type { WorksheetColumn } from './model/dataset';

type WorksheetColumnRole = 'row-number' | 'selection' | 'row-id' | 'data';

export interface ColumnModelEntry {
  key: string;
  label: string;
  role: WorksheetColumnRole;
  width: number;
}

const SYSTEM_COLUMNS: ColumnModelEntry[] = [
  { key: '__rowNumber', label: '', role: 'row-number', width: 44 },
  { key: '__selection', label: '✓', role: 'selection', width: 34 },
  { key: 'rowId', label: 'rowId', role: 'row-id', width: 164 },
];

export function buildColumnModel(columns: WorksheetColumn[]): ColumnModelEntry[] {
  return [
    ...SYSTEM_COLUMNS,
    ...columns.map((column) => ({
      key: column.key,
      label: column.key,
      role: 'data' as const,
      width: Math.max(120, Math.min(220, column.key.length * 9 + 28)),
    })),
  ];
}

export function columnGridTemplate(model: ColumnModelEntry[]) {
  return model.map((column) => `${column.width}px`).join(' ');
}

export function columnModelWidth(model: ColumnModelEntry[]) {
  return model.reduce((total, column) => total + column.width, 0);
}
