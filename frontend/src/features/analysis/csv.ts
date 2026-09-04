function csvValue(value: unknown) {
  if (value == null) return '';
  const text = typeof value === 'object' ? JSON.stringify(value) : String(value);
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

const INTERNAL_COLUMNS = new Set(['rowId', '__rowId', 'datasetId']);

function worksheetColumns(rows: Record<string, unknown>[]) {
  const columnSet = new Set<string>();
  for (const row of rows) {
    Object.keys(row).forEach((column) => {
      if (!INTERNAL_COLUMNS.has(column)) columnSet.add(column);
    });
  }
  return [...columnSet];
}

export function serializeWorksheetCsv(rows: Record<string, unknown>[]) {
  const columns = worksheetColumns(rows);
  const lines = [columns.map(csvValue).join(',')];
  for (const row of rows) lines.push(columns.map((column) => csvValue(row[column])).join(','));
  return `\ufeff${lines.join('\r\n')}`;
}

function tsvValue(value: unknown) {
  if (value == null) return '';
  const text = typeof value === 'object' ? JSON.stringify(value) : String(value);
  return /["\t\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function serializeWorksheetTsv(rows: Record<string, unknown>[]) {
  const columns = worksheetColumns(rows);
  const lines = [columns.map(tsvValue).join('\t')];
  for (const row of rows) lines.push(columns.map((column) => tsvValue(row[column])).join('\t'));
  return lines.join('\r\n');
}
