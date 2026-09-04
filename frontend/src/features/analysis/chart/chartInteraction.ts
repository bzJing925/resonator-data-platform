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

export function buildYAxisAutorangeUpdate(layout: Record<string, unknown>) {
  return Object.keys(layout)
    .filter((key) => /^yaxis\d*$/.test(key))
    .reduce<Record<string, true>>((update, key) => {
      update[`${key}.autorange`] = true;
      return update;
    }, {});
}
