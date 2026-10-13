/** 浏览器端两阶段代理模型推理（规格 §5.2）。
 *  数据文件在 /webdata/（public 目录随构建进 dist）。懒加载 onnxruntime-web。
 *  GitHub Pages 无 COOP/COEP → SharedArrayBuffer 不可用 → 强制单线程 wasm。
 *  测试可注入 _loaders 覆盖数据加载（vitest node 环境从磁盘读）。 */

import { describeModel, type ModelDescription } from '../features/analysis/report/modelDescription';
import { deterministicSamplePositions } from './sampling';
import { baselineLookup } from './baseline';
import { cubicSplineInterpolate } from './spline';

const COND_IDX = [0, 1, 3, 4, 2]; // [fs, fp, Qs, Qp, kt2]
const PARAM_COLS = ['fs_ghz', 'fp_ghz', 'k2eff_pct', 'qs', 'qp', 'qs_bodeq', 'qp_bodeq'];

interface Loaders {
  json: (path: string) => Promise<any>;
  bytes: (path: string) => Promise<ArrayBuffer>;
}

const browserLoaders: Loaders = {
  json: (p) => fetch(`webdata/${p}`).then((r) => {
    if (!r.ok) throw new Error(`快照缺失: ${p}`);
    return r.json();
  }),
  bytes: (p) => fetch(`webdata/${p}`).then((r) => {
    if (!r.ok) throw new Error(`快照缺失: ${p}`);
    return r.arrayBuffer();
  }),
};

let _loaders: Loaders = browserLoaders;

/** 测试专用：注入磁盘加载器。 */
export function __setLoadersForTest(l: Loaders | null): void {
  _loaders = l ?? browserLoaders;
  _data = null;
}

interface WebData {
  metadata: unknown;
  freq: number[];
  k: number;
  xMean: number[];
  xStd: number[];
  pMean: number[];
  pStd: number[];
  zMean: number;
  zStd: number;
  pfs: number[];
  areas: number[];
  paramsBase: number[][][];
  zBaseFull: number[][][];
  s1: any; // ort.InferenceSession
  s2: any;
}

let _data: Promise<WebData> | null = null;

async function load(): Promise<WebData> {
  const [meta, baseline, ort] = await Promise.all([
    _loaders.json('surr_meta.json'),
    _loaders.json('baseline.json'),
    import('onnxruntime-web/wasm'),
  ]);
  ort.env.wasm.numThreads = 1; // 静态托管无 crossOriginIsolation，禁用多线程
  const [b1, b2] = await Promise.all([
    _loaders.bytes('stage1.onnx'),
    _loaders.bytes('stage2.onnx'),
  ]);
  const [s1, s2] = await Promise.all([
    ort.InferenceSession.create(new Uint8Array(b1)),
    ort.InferenceSession.create(new Uint8Array(b2)),
  ]);
  const freq: number[] = meta.freq_ghz;
  const zBaseFull: number[][][] = baseline.z_base_full;
  if (zBaseFull[0][0].length !== freq.length) {
    throw new Error('baseline.json 频率网格与 surr_meta 不一致，请重新导出 webdata');
  }
  return {
    metadata: meta.metadata ?? null,
    freq,
    k: meta.k,
    xMean: meta.x_mean,
    xStd: meta.x_std,
    pMean: meta.params_mean,
    pStd: meta.params_std,
    zMean: meta.z_mean[0],
    zStd: meta.z_std[0],
    pfs: baseline.pfs,
    areas: baseline.areas,
    paramsBase: baseline.params_base,
    zBaseFull,
    s1,
    s2,
  };
}

function getData(): Promise<WebData> {
  if (!_data) _data = load();
  return _data;
}

export interface OnnxPredictResult {
  metadata: ModelDescription;
  params: Record<string, number>;
  freq_ghz: number[];
  z_db: number[];
  z_base: number[];
  sample_freq: number[];
  sample_z: number[];
}

export async function surrogatePredictOnnx(
  eg: number,
  fl: number,
  area: number,
  pf = 0,
  ag = 1.0,
): Promise<OnnxPredictResult> {
  const d = await getData();
  const ort = await import('onnxruntime-web/wasm');
  const x = [eg, fl, ag, area, pf].map((v, i) => (v - d.xMean[i]) / d.xStd[i]);
  const xT = new ort.Tensor('float32', Float32Array.from(x), [1, 5]);

  const [paramsBase, zBaseFull] = baselineLookup(
    d.pfs, d.areas, d.paramsBase, d.zBaseFull, area, pf);
  const pbN = paramsBase.map((v, i) => (v - d.pMean[i]) / d.pStd[i]);
  const pbT = new ort.Tensor('float32', Float32Array.from(pbN), [1, 7]);

  // 第一遍：性能参数（sparse_base 置零，params 头不依赖它）
  const zeroSb = new ort.Tensor('float32', new Float32Array(d.k), [1, d.k]);
  const out1 = await d.s1.run({ x: xT, params_base: pbT, sparse_base: zeroSb });
  const paramsArr = Array.from(out1.params.data as Float32Array)
    .map((v, i) => v * d.pStd[i] + d.pMean[i]);
  const fs = paramsArr[0];
  const fp = paramsArr[1];

  // 预测 fs/fp → 采样位置 → sparse_base（dB → 归一化；webdata 频率轴与基准表同网格）
  const pos = deterministicSamplePositions(d.freq, fs, fp, d.k);
  const sbDb = pos.map((i) => zBaseFull[i]);
  const sbT = new ort.Tensor(
    'float32',
    Float32Array.from(sbDb.map((v) => (v - d.zMean) / d.zStd)),
    [1, d.k],
  );

  // 第二遍：稀疏点
  const out2 = await d.s1.run({ x: xT, params_base: pbT, sparse_base: sbT });
  const sparseZ = Array.from(out2.sparse.data as Float32Array)
    .map((v) => v * d.zStd + d.zMean);

  // Stage 2：cond 归一化 + 样条基线（归一化空间）
  const cond = COND_IDX.map((i) => (paramsArr[i] - d.pMean[i]) / d.pStd[i]);
  const sfreq = pos.map((i) => d.freq[i]);
  const szN = sparseZ.map((v) => (v - d.zMean) / d.zStd);
  const samplesFlat = new Float32Array(d.k * 2);
  for (let i = 0; i < d.k; i++) {
    samplesFlat[i * 2] = sfreq[i];
    samplesFlat[i * 2 + 1] = szN[i];
  }
  const zbN = cubicSplineInterpolate(sfreq, szN, d.freq);
  const feeds = {
    cond: new ort.Tensor('float32', Float32Array.from(cond), [1, 5]),
    samples: new ort.Tensor('float32', samplesFlat, [1, d.k, 2]),
    freq_target: new ort.Tensor('float32', Float32Array.from(d.freq), [1, d.freq.length]),
    z_baseline: new ort.Tensor('float32', Float32Array.from(zbN), [1, d.freq.length]),
  };
  const out3 = await d.s2.run(feeds);
  const zDb = Array.from(out3.z.data as Float32Array).map((v) => v * d.zStd + d.zMean);

  const params: Record<string, number> = {};
  PARAM_COLS.forEach((c, i) => {
    params[c] = paramsArr[i];
  });
  return {
    metadata: describeModel(d.metadata, { eg, fl, ag, area, pf }),
    params,
    freq_ghz: d.freq,
    z_db: zDb,
    z_base: zBaseFull,
    sample_freq: sfreq,
    sample_z: sparseZ,
  };
}
