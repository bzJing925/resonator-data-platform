import { describe, it, expect, beforeAll } from 'vitest';
import { readFile, access } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { surrogatePredictOnnx, __setLoadersForTest } from '../surrogateOnnx';
import fixture from '../__fixtures__/surrogate.json';

const HERE = dirname(fileURLToPath(import.meta.url));
const WEBDATA = join(HERE, '../../../public/webdata');

// webdata 是构建产物（gitignored）：先跑 export_web_snapshot.py + export_surrogate_onnx.py
async function webdataReady(): Promise<boolean> {
  try {
    await access(join(WEBDATA, 'stage1.onnx'));
    return true;
  } catch {
    return false;
  }
}
const HAS_WEBDATA = await webdataReady();

beforeAll(() => {
  __setLoadersForTest({
    json: async (p: string) => JSON.parse(await readFile(join(WEBDATA, p), 'utf-8')),
    bytes: async (p: string) => {
      const b = await readFile(join(WEBDATA, p));
      return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength);
    },
  });
});

describe('ONNX 端到端对拍（vs Python SurrogatePredictor）', () => {
  it.skipIf(fixture.cases.length === 0 || !HAS_WEBDATA)('5 组格内输入', async () => {
    for (const c of fixture.cases) {
      const r = await surrogatePredictOnnx(c.eg, c.fl, c.area, c.pf);
      for (const [k, v] of Object.entries(c.params)) {
        expect(Math.abs((r.params as any)[k] - v)).toBeLessThan(
          k.includes('ghz') ? 0.001 : Math.abs(v) * 0.001 + 1e-3);
      }
      r.z_db.forEach((v, i) => expect(Math.abs(v - c.z_db[i])).toBeLessThan(0.05));
      expect(r.sample_freq).toEqual(c.sample_freq);
    }
  }, 120_000);
});
