/** 确定性稀疏采样位置（移植 app/core/sparse_sampler.py，行为逐点一致）。 */

/** numpy round-half-even（与 np.round / Python round 一致；Math.round 是 half-up，不能直接换）。 */
function npRound(x: number): number {
  const f = Math.floor(x);
  const d = x - f;
  if (d > 0.5) return f + 1;
  if (d < 0.5) return f;
  return f % 2 === 0 ? f : f + 1; // 恰 .5 → 偶数
}

function linspaceInts(lo: number, hi: number, m: number): number[] {
  if (m <= 0) return [];
  if (m === 1) return [npRound(lo)];
  const step = (hi - lo) / (m - 1);
  return Array.from({ length: m }, (_, i) => npRound(lo + i * step));
}

function take(idx: number[], m: number): number[] {
  if (m <= 0 || idx.length === 0) return [];
  const sel = linspaceInts(0, idx.length - 1, Math.min(m, idx.length));
  return sel.map((i) => idx[i]);
}

export function deterministicSamplePositions(
  freqGhz: number[],
  fs: number,
  fp: number,
  k = 256,
  denseRatio = 0.6,
  transitionRatio = 0.25,
  denseHalfBw = 0.5,
  transitionHalfBw = 1.5,
): number[] {
  const n = freqGhz.length;
  k = Math.min(k, n);
  if (fs <= 0 || fp <= fs) {
    return linspaceInts(0, n - 1, k);
  }
  const delta = fp - fs;
  const fmin = freqGhz[0];
  const fmax = freqGhz[n - 1];
  const denseLo = Math.max(fs - denseHalfBw * delta, fmin);
  const denseHi = Math.min(fp + denseHalfBw * delta, fmax);
  const transLo = Math.max(fs - transitionHalfBw * delta, fmin);
  const transHi = Math.min(fp + transitionHalfBw * delta, fmax);

  const inRange = (lo: number, hi: number) =>
    freqGhz.map((f, i) => (f >= lo && f <= hi ? i : -1)).filter((i) => i >= 0);

  const denseIdx = inRange(denseLo, denseHi);
  const transIdx = [
    ...inRange(transLo, denseLo - 1e-12),
    ...inRange(denseHi + 1e-12, transHi),
  ];
  const occupied = new Set([...denseIdx, ...transIdx]);
  const farIdx = freqGhz.map((_, i) => i).filter((i) => !occupied.has(i));

  const qDense = npRound(k * denseRatio);
  const qTrans = npRound(k * transitionRatio);
  const qFar = k - qDense - qTrans;

  let takeDense = Math.min(qDense, denseIdx.length);
  let takeTrans = Math.min(qTrans, transIdx.length);
  let takeFar = Math.min(qFar, farIdx.length);

  let leftover = k - (takeDense + takeTrans + takeFar);
  const caps: [string, number][] = [
    ['dense', denseIdx.length - takeDense],
    ['trans', transIdx.length - takeTrans],
    ['far', farIdx.length - takeFar],
  ];
  while (leftover > 0 && caps.some(([, c]) => c > 0)) {
    for (let i = 0; i < caps.length; i++) {
      if (leftover <= 0) break;
      const [name, cap] = caps[i];
      if (cap <= 0) continue;
      if (name === 'dense') takeDense++;
      else if (name === 'trans') takeTrans++;
      else takeFar++;
      caps[i] = [name, cap - 1];
      leftover--;
    }
  }
  const result = [
    ...take(denseIdx, takeDense),
    ...take(transIdx, takeTrans),
    ...take(farIdx, takeFar),
  ];
  return result.sort((a, b) => a - b);
}
