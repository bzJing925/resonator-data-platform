import { describe, it, expect } from 'vitest';
import { deterministicSamplePositions } from '../sampling';
import fixture from '../__fixtures__/sampling.json';

describe('deterministicSamplePositions 黄金对拍', () => {
  const freq = fixture.freq_ghz;
  for (const c of fixture.cases) {
    it(`fs=${c.fs} fp=${c.fp}`, () => {
      const pos = deterministicSamplePositions(freq, c.fs, c.fp, c.k);
      expect(pos).toEqual(c.positions);
    });
  }
});
