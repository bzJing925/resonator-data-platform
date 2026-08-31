import { describe, it, expect } from 'vitest';
import { baselineLookup } from '../baseline';
import fixture from '../__fixtures__/baseline.json';

describe('baselineLookup 黄金对拍', () => {
  for (const c of fixture.cases) {
    it(`area=${c.area} pf=${c.pf}`, () => {
      const [p, z] = baselineLookup(
        fixture.pfs,
        fixture.areas,
        fixture.params_base,
        fixture.z_base_full,
        c.area,
        c.pf,
      );
      p.forEach((v, i) => expect(v).toBeCloseTo(c.params[i], 10));
      z.forEach((v, i) => expect(v).toBeCloseTo(c.z[i], 10));
    });
  }
});
