import type { AnalysisRow } from './workspace';

function csvValue(value: unknown) {
  if (value == null) return '';
  const text = typeof value === 'object' ? JSON.stringify(value) : String(value);
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function serializeWorksheetCsv(rows: AnalysisRow[]) {
  const columnSet = new Set<string>();
  for (const row of rows) {
    Object.keys(row).forEach((column) => {
      if (column !== '__rowId') columnSet.add(column);
    });
  }
  const columns = [...columnSet];
  const lines = [columns.map(csvValue).join(',')];
  for (const row of rows) lines.push(columns.map((column) => csvValue(row[column])).join(','));
  return `\ufeff${lines.join('\r\n')}`;
}
