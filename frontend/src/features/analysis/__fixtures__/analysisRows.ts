export const ANALYSIS_CHART_TYPES = ['scatter', 'box', 'violin', 'swarm', 'line'] as const;

export const STRUCTURE_TYPE_FIELD = {
  name: 'structureType',
  label: '结构类型',
  isCategorical: true,
};

export const AREA_REALITY_FIELD = {
  name: 'areaReality',
  label: '实际面积',
  isCategorical: true,
};

export const ANALYSIS_Y_FIELDS = [
  { name: 'qs', label: 'Qs', isCategorical: false },
  { name: 'qp', label: 'Qp', isCategorical: false },
  { name: 'k2eff_pct', label: 'k²eff(%)', isCategorical: false },
];

export const ANALYSIS_ROWS = Array.from({ length: 24 }, (_, index) => {
  const structureType = index % 2 === 0 ? 'Series' : 'Parallel';
  const areaReality = [700, 900, 1200][index % 3];
  return {
    id: index + 1,
    rowId: `device:${index + 1}`,
    __rowId: `device:${index + 1}`,
    datasetId: 'phase-0-fixture',
    structureType,
    areaReality,
    // Keep the current backend source field in the fixture as well as the
    // canonical analysis field so contract drift is visible in tests.
    area_um2: areaReality,
    qs: 800 + index * 17,
    qp: 600 + index * 13,
    k2eff_pct: 3.5 + (index % 8) * 0.2,
  };
});

export function buildPerformanceRows(rowCount: number) {
  return Array.from({ length: rowCount }, (_, index) => ({
    id: index + 1,
    rowId: `device:${index + 1}`,
    __rowId: `device:${index + 1}`,
    datasetId: 'phase-0-performance',
    structureType: index % 2 === 0 ? 'Series' : 'Parallel',
    areaReality: [700, 900, 1200, 1500][index % 4],
    area_um2: [700, 900, 1200, 1500][index % 4],
    qs: 700 + Math.sin(index / 19) * 180 + (index % 31),
    qp: 550 + Math.cos(index / 23) * 140 + (index % 29),
    k2eff_pct: 3 + (index % 40) * 0.08,
  }));
}
