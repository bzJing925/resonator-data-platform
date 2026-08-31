/** 基准表查找（移植 BaselineTable.lookup）：pf 取最近档，面积维线性插值、越界端点。 */
export function baselineLookup(
  pfs: number[],
  areas: number[],
  paramsBase: number[][][], // (P, A, 7)
  zBaseFull: number[][][], // (P, A, N)
  area: number,
  pf: number,
): [number[], number[]] {
  let pi = 0;
  for (let i = 1; i < pfs.length; i++) {
    if (Math.abs(pfs[i] - pf) < Math.abs(pfs[pi] - pf)) pi = i;
  }
  const a = area;
  if (a <= areas[0]) return [[...paramsBase[pi][0]], [...zBaseFull[pi][0]]];
  if (a >= areas[areas.length - 1]) {
    const last = areas.length - 1;
    return [[...paramsBase[pi][last]], [...zBaseFull[pi][last]]];
  }
  let hi = 0;
  while (areas[hi] < a) hi++; // searchsorted 等价
  const lo = hi - 1;
  const w = (a - areas[lo]) / (areas[hi] - areas[lo]);
  const params = paramsBase[pi][lo].map((v, j) => (1 - w) * v + w * paramsBase[pi][hi][j]);
  const z = zBaseFull[pi][lo].map((v, j) => (1 - w) * v + w * zBaseFull[pi][hi][j]);
  return [params, z];
}
