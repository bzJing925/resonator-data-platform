import { describe, it, expect } from 'vitest';
import { cubicSplineInterpolate } from '../spline';
import fixture from '../__fixtures__/spline.json';

describe('cubicSplineInterpolate（not-a-knot，对齐 scipy）', () => {
  const freq = fixture.freq_ghz;
  for (const [ci, c] of fixture.cases.entries()) {
    it(`case ${ci}（${c.xs.length} 点）`, () => {
      const out = cubicSplineInterpolate(c.xs, c.ys, freq);
      out.forEach((v, i) => expect(Math.abs(v - c.out[i])).toBeLessThan(1e-6));
    });
  }
});
