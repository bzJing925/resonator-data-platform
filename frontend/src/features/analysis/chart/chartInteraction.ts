export function datumRowIds(datum: unknown): string[] {
  if (typeof datum === 'string') return [datum];
  if (!datum || typeof datum !== 'object') return [];
  const value = datum as Record<string, unknown>;
  if (Array.isArray(value.__memberRowIds)) {
    return value.__memberRowIds.filter((rowId): rowId is string => typeof rowId === 'string');
  }
  const rowId = value.rowId ?? value.__rowId;
  return typeof rowId === 'string' && rowId ? [rowId] : [];
}

function isSelectableTrace(trace: any) {
  const layer = trace?.meta?.analysisLayer;
  if (layer) return layer === 'raw-points';
  return Array.isArray(trace?.customdata);
}

export function buildSelectionRestyle(traces: any[], selectedRowIds?: Set<string>) {
  const traceIndices: number[] = [];
  const selectedpoints: Array<number[] | null> = [];
  const hasSelection = !!selectedRowIds?.size;
  traces.forEach((trace, traceIndex) => {
    if (!isSelectableTrace(trace)) return;
    const customdata = Array.isArray(trace.customdata) ? trace.customdata : [];
    traceIndices.push(traceIndex);
    selectedpoints.push(hasSelection
      ? customdata.flatMap((datum, pointIndex) =>
        datumRowIds(datum).some((rowId) => selectedRowIds?.has(rowId)) ? [pointIndex] : [])
      : null);
  });
  return { traceIndices, selectedpoints };
}

export type YAxisRanges = Record<string, [number, number]>;

export function buildYDataRanges(
  rows: Record<string, unknown>[],
  fields: Array<{ name: string }>,
): YAxisRanges {
  const ranges: YAxisRanges = {};
  for (const field of fields) {
    let min = Infinity;
    let max = -Infinity;
    for (const row of rows) {
      const value = row[field.name];
      if (typeof value !== 'number' || !Number.isFinite(value)) continue;
      min = Math.min(min, value);
      max = Math.max(max, value);
    }
    if (Number.isFinite(min)) ranges[field.name] = [min - 5, max + 5];
  }
  return ranges;
}

export function buildYAxisLayoutRanges(
  layout: any,
  fields: Array<{ name: string }>,
  ranges: YAxisRanges,
): YAxisRanges {
  const axes: YAxisRanges = {};
  const columns = layout.grid?.columns || 1;
  for (const key of Object.keys(layout)) {
    if (!/^yaxis\d*$/.test(key) || layout[key]?.type === 'category') continue;
    const index = Number(key.slice(5) || 1) - 1;
    const field = fields[Math.floor(index / columns) % fields.length];
    if (field && ranges[field.name]) axes[key] = ranges[field.name];
  }
  return axes;
}

export function buildYAxisAutorangeUpdate(layout: any, traces: any[] = []) {
  return Object.keys(layout)
    .filter((key) => /^yaxis\d*$/.test(key))
    .reduce<Record<string, unknown>>((update, key) => {
      let range = layout.meta?.analysisYRanges?.[key];
      if (!range && layout[key]?.type !== 'category') {
        let min = Infinity;
        let max = -Infinity;
        const ref = key.replace('axis', '');
        for (const trace of traces) {
          if ((trace.yaxis || 'y') !== ref) continue;
          for (const value of trace.y || []) {
            if (typeof value !== 'number' || !Number.isFinite(value)) continue;
            min = Math.min(min, value);
            max = Math.max(max, value);
          }
        }
        if (Number.isFinite(min)) range = [min - 5, max + 5];
      }
      update[`${key}.autorange`] = !range;
      if (range) update[`${key}.range`] = range;
      return update;
    }, {});
}
