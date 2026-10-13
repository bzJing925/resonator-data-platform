/* AlN 代理模型 —— 离线单文件版应用逻辑（原生 JS，无框架、无 import）。
 *
 * 逐一移植自平台前端（行为须与之一致，改动时对照源文件）：
 *   frontend/src/ml/surrogateOnnx.ts      —— 两阶段 ONNX 推理链
 *   frontend/src/ml/sampling.ts           —— 确定性稀疏采样位置
 *   frontend/src/ml/baseline.ts           —— 基准表查找
 *   frontend/src/ml/spline.ts             —— not-a-knot 三次样条
 *   frontend/src/features/analysis/report/modelDescription.ts —— 模型元信息
 *   frontend/src/pages/Surrogate.tsx      —— 页面 UI
 *
 * 由 build.py 内联的的全局常量（见 DATA_JS）：
 *   SURR_META, BASELINE, UI_META, CELL_MEANS(nullable),
 *   STAGE1_B64, STAGE2_B64, ORT_WASM_B64, ORT_GLUE_B64
 * 以及脚本全局：Plotly（plotly.min.js）、ort（onnxruntime-web UMD 版）。
 */
/* global Plotly, ort, SURR_META, BASELINE, UI_META, CELL_MEANS,
          STAGE1_B64, STAGE2_B64, ORT_WASM_B64, ORT_GLUE_B64 */
(function () {
  'use strict';

  /* ==================================================================
   * base64 → Uint8Array
   * ================================================================== */
  function b64ToBytes(b64) {
    const bin = atob(b64);
    const n = bin.length;
    const out = new Uint8Array(n);
    for (let i = 0; i < n; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  /* ==================================================================
   * ml/sampling.ts 移植：确定性稀疏采样位置
   * ================================================================== */
  /** numpy round-half-even（与 np.round / Python round 一致；Math.round 是 half-up，不能直接换）。 */
  function npRound(x) {
    const f = Math.floor(x);
    const d = x - f;
    if (d > 0.5) return f + 1;
    if (d < 0.5) return f;
    return f % 2 === 0 ? f : f + 1; // 恰 .5 → 偶数
  }

  function linspaceInts(lo, hi, m) {
    if (m <= 0) return [];
    if (m === 1) return [npRound(lo)];
    const step = (hi - lo) / (m - 1);
    return Array.from({ length: m }, (_, i) => npRound(lo + i * step));
  }

  function take(idx, m) {
    if (m <= 0 || idx.length === 0) return [];
    const sel = linspaceInts(0, idx.length - 1, Math.min(m, idx.length));
    return sel.map((i) => idx[i]);
  }

  function deterministicSamplePositions(
    freqGhz, fs, fp, k = 256,
    denseRatio = 0.6, transitionRatio = 0.25,
    denseHalfBw = 0.5, transitionHalfBw = 1.5,
  ) {
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

    const inRange = (lo, hi) =>
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
    const caps = [
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

  /* ==================================================================
   * ml/baseline.ts 移植：基准表查找（pf 最近档，面积线性插值、越界端点）
   * ================================================================== */
  function baselineLookup(pfs, areas, paramsBase, zBaseFull, area, pf) {
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

  /* ==================================================================
   * ml/spline.ts 移植：not-a-knot 三次样条（对齐 scipy CubicSpline 默认）
   * ================================================================== */
  function solveDense(a, b) {
    // 部分主元高斯消元（样条矩阵对角占优，稳定性足够）
    const n = b.length;
    const m = a.map((row, i) => [...row, b[i]]);
    for (let col = 0; col < n; col++) {
      let piv = col;
      for (let r = col + 1; r < n; r++) {
        if (Math.abs(m[r][col]) > Math.abs(m[piv][col])) piv = r;
      }
      [m[col], m[piv]] = [m[piv], m[col]];
      for (let r = 0; r < n; r++) {
        if (r === col) continue;
        const f = m[r][col] / m[col][col];
        for (let c = col; c <= n; c++) m[r][c] -= f * m[col][c];
      }
    }
    return m.map((row, i) => {
      if (m[i][i] === 0) throw new Error('样条矩阵奇异');
      return row[n] / m[i][i];
    });
  }

  function cubicSplineInterpolate(xs, ys, target) {
    const n = xs.length;
    if (n < 4) {
      return target.map((t) => {
        if (t <= xs[0]) return ys[0] + ((ys[1] - ys[0]) * (t - xs[0])) / (xs[1] - xs[0]);
        if (t >= xs[n - 1]) {
          return ys[n - 1] + ((ys[n - 1] - ys[n - 2]) * (t - xs[n - 1])) / (xs[n - 1] - xs[n - 2]);
        }
        let i = 0;
        while (xs[i + 1] < t) i++;
        const w = (t - xs[i]) / (xs[i + 1] - xs[i]);
        return ys[i] * (1 - w) + ys[i + 1] * w;
      });
    }
    // Burden 形式：S_i(x) = a_i + b_i·dx + c_i·dx² + d_i·dx³，解 c（=S''/2）
    const h = Array.from({ length: n - 1 }, (_, i) => xs[i + 1] - xs[i]);
    const A = Array.from({ length: n }, () => new Array(n).fill(0));
    const rhs = new Array(n).fill(0);
    // not-a-knot 首行：h1·c0 − (h0+h1)·c1 + h0·c2 = 0
    A[0][0] = h[1];
    A[0][1] = -(h[0] + h[1]);
    A[0][2] = h[0];
    for (let i = 1; i < n - 1; i++) {
      A[i][i - 1] = h[i - 1];
      A[i][i] = 2 * (h[i - 1] + h[i]);
      A[i][i + 1] = h[i];
      rhs[i] = 3 * ((ys[i + 1] - ys[i]) / h[i] - (ys[i] - ys[i - 1]) / h[i - 1]);
    }
    // not-a-knot 末行
    A[n - 1][n - 3] = h[n - 2];
    A[n - 1][n - 2] = -(h[n - 3] + h[n - 2]);
    A[n - 1][n - 1] = h[n - 3];
    const c = solveDense(A, rhs);
    const b = new Array(n - 1);
    const d = new Array(n - 1);
    for (let i = 0; i < n - 1; i++) {
      b[i] = (ys[i + 1] - ys[i]) / h[i] - (h[i] * (2 * c[i] + c[i + 1])) / 3;
      d[i] = (c[i + 1] - c[i]) / (3 * h[i]);
    }
    return target.map((t) => {
      let i = 0;
      if (t <= xs[0]) i = 0;
      else if (t >= xs[n - 1]) i = n - 2;
      else {
        while (xs[i + 1] < t) i++;
      }
      const dx = t - xs[i];
      return ys[i] + b[i] * dx + c[i] * dx * dx + d[i] * dx * dx * dx;
    });
  }

  /* ==================================================================
   * modelDescription.ts 移植：模型元信息描述
   * ================================================================== */
  function describeModel(value, inputs) {
    const unknown = {
      schema_version: 1, model_digest: null, training_range: null,
      training_source: 'unknown', validation_status: 'unknown',
      validation_metrics: null, validation_dataset_digest: null,
      applicability: 'unknown',
    };
    if (!value || typeof value !== 'object' || Array.isArray(value)) return unknown;
    const v = value;
    if (v.schema_version !== 1) return unknown;
    const digest = (x) => (typeof x === 'string' && /^[a-f0-9]{64}$/i.test(x) ? x : null);
    const range =
      v.training_range_status === 'known' && v.training_range && typeof v.training_range === 'object' &&
      Object.values(v.training_range).every(
        (b) => Array.isArray(b) && b.length === 2 && b.every(Number.isFinite) && b[0] <= b[1])
      ? structuredClone(v.training_range) : null;
    const metrics =
      v.validation_metrics && typeof v.validation_metrics === 'object' && !Array.isArray(v.validation_metrics) &&
      Object.values(v.validation_metrics).every(
        (x) => x === null || (typeof x === 'number' && Number.isFinite(x)))
      ? structuredClone(v.validation_metrics) : null;
    const model = {
      ...unknown,
      model_digest: digest(v.model_digest),
      training_range: range,
      training_source: ['measured', 'simulation', 'mixed'].includes(v.training_source)
        ? v.training_source : 'unknown',
      validation_status: ['verified', 'unverified'].includes(v.validation_status)
        ? v.validation_status : 'unknown',
      validation_metrics: metrics,
      validation_dataset_digest: digest(v.validation_dataset_digest),
    };
    if (model.validation_status === 'verified' &&
        (!model.model_digest || !model.validation_dataset_digest || !metrics)) {
      model.validation_status = 'unverified';
    }
    if (inputs && range && Object.keys(range).length) {
      const entries = Object.entries(range);
      model.applicability = entries.some(
        ([key, [low, high]]) => Number.isFinite(inputs[key]) && (inputs[key] < low || inputs[key] > high))
        ? 'out_of_range'
        : entries.every(([key]) => Number.isFinite(inputs[key])) ? 'within_range' : 'unknown';
    } else if (['within_range', 'out_of_range'].includes(v.applicability)) {
      model.applicability = range ? v.applicability : 'unknown';
    }
    return model;
  }

  function modelDescriptionText(model) {
    return `模型版本：${model.model_digest ?? '未知'}；训练范围：${model.training_range ? JSON.stringify(model.training_range) : '未知/未提供'}；训练数据：${model.training_source}；验证：${model.validation_status}；验证指标：${model.validation_metrics ? JSON.stringify(model.validation_metrics) : '未知/未提供'}；适用性：${model.applicability}。未提供预测置信区间。`;
  }

  /* ==================================================================
   * surrogateOnnx.ts 移植：两阶段 ONNX 推理链
   * ================================================================== */
  const COND_IDX = [0, 1, 3, 4, 2]; // [fs, fp, Qs, Qp, kt2]
  const PARAM_COLS = ['fs_ghz', 'fp_ghz', 'k2eff_pct', 'qs', 'qp', 'qs_bodeq', 'qp_bodeq'];

  let _data = null; // 模型与数据，加载一次

  async function load() {
    const meta = SURR_META;
    const baseline = BASELINE;
    ort.env.wasm.numThreads = 1; // 无 crossOriginIsolation，禁用多线程
    ort.env.wasm.proxy = false; // file:// 下不走 worker，主线程初始化
    const glueUrl = URL.createObjectURL(
      new Blob([b64ToBytes(ORT_GLUE_B64)], { type: 'text/javascript' }));
    ort.env.wasm.wasmPaths = { mjs: glueUrl };
    ort.env.wasm.wasmBinary = b64ToBytes(ORT_WASM_B64);
    const [s1, s2] = await Promise.all([
      ort.InferenceSession.create(b64ToBytes(STAGE1_B64)),
      ort.InferenceSession.create(b64ToBytes(STAGE2_B64)),
    ]);
    const freq = meta.freq_ghz;
    const zBaseFull = baseline.z_base_full;
    if (zBaseFull[0][0].length !== freq.length) {
      throw new Error('baseline 频率网格与 surr_meta 不一致，请重新导出数据');
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

  function getData() {
    if (!_data) _data = load();
    return _data;
  }

  async function predict(eg, fl, area, pf, ag) {
    const d = await getData();
    const x = [eg, fl, ag, area, pf].map((v, i) => (v - d.xMean[i]) / d.xStd[i]);
    const xT = new ort.Tensor('float32', Float32Array.from(x), [1, 5]);

    const [paramsBase, zBaseFull] = baselineLookup(
      d.pfs, d.areas, d.paramsBase, d.zBaseFull, area, pf);
    const pbN = paramsBase.map((v, i) => (v - d.pMean[i]) / d.pStd[i]);
    const pbT = new ort.Tensor('float32', Float32Array.from(pbN), [1, 7]);

    // 第一遍：性能参数（sparse_base 置零，params 头不依赖它）
    const zeroSb = new ort.Tensor('float32', new Float32Array(d.k), [1, d.k]);
    const out1 = await d.s1.run({ x: xT, params_base: pbT, sparse_base: zeroSb });
    const paramsArr = Array.from(out1.params.data)
      .map((v, i) => v * d.pStd[i] + d.pMean[i]);
    const fs = paramsArr[0];
    const fp = paramsArr[1];

    // 预测 fs/fp → 采样位置 → sparse_base（dB → 归一化）
    const pos = deterministicSamplePositions(d.freq, fs, fp, d.k);
    const sbDb = pos.map((i) => zBaseFull[i]);
    const sbT = new ort.Tensor(
      'float32',
      Float32Array.from(sbDb.map((v) => (v - d.zMean) / d.zStd)),
      [1, d.k],
    );

    // 第二遍：稀疏点
    const out2 = await d.s1.run({ x: xT, params_base: pbT, sparse_base: sbT });
    const sparseZ = Array.from(out2.sparse.data)
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
    const zDb = Array.from(out3.z.data).map((v) => v * d.zStd + d.zMean);

    const params = {};
    PARAM_COLS.forEach((c, i) => {
      params[c] = paramsArr[i];
    });
    return {
      params,
      freq_ghz: d.freq,
      z_db: zDb,
      z_base: zBaseFull,
      sample_freq: sfreq,
      sample_z: sparseZ,
    };
  }

  /* ==================================================================
   * Surrogate.tsx 移植：页面 UI
   * ================================================================== */
  const PARAM_LABELS = [
    ['fs_ghz', 5], ['fp_ghz', 5], ['k2eff_pct', 4], ['qs', 5],
    ['qp', 5], ['qs_bodeq', 5], ['qp_bodeq', 5],
  ];
  const DEBOUNCE_MS = 150;

  const $ = (id) => document.getElementById(id);
  const els = {
    metaDesc: $('meta-desc'),
    egSlider: $('eg-slider'), egNum: $('eg-num'), egVal: $('eg-val'),
    flSlider: $('fl-slider'), flNum: $('fl-num'), flVal: $('fl-val'),
    agNum: $('ag-num'), areaSel: $('area-sel'), pfSel: $('pf-sel'),
    showBase: $('show-base'), showPts: $('show-pts'), showTrue: $('show-true'),
    truthWrap: $('truth-wrap'), spinner: $('spinner'),
    warnGrid: $('warn-grid'), warnSim: $('warn-sim'), simArea: $('sim-area'),
    chart: $('chart'), tableWrap: $('table-wrap'), paramRow: $('param-row'),
    loading: $('loading'),
  };

  const state = {
    eg: 1.0,
    fl: 0.5,
    ag: 1.0,
    area: UI_META.areas[Math.floor(UI_META.areas.length / 2)],
    pf: 0,
    showBase: true,
    showPts: true,
    showTrue: true,
  };

  let lastResult = null;   // 最近一次成功预测（含真值叠加数据）
  let lastTruth = null;    // 当前输入命中的格均值真值谱（无则 null）
  let lastInGrid = false;

  // 与静态版一致的容差匹配（eg/fl 源自 float32 数据集，导出时已收敛到 4 位小数）
  function findCell(list, eg, fl, area, pf) {
    if (!list) return null;
    for (const c of list) {
      if (Math.abs(c[0] - eg) < 1e-6 && Math.abs(c[1] - fl) < 1e-6 &&
          c[2] === area && c[3] === pf) return c;
    }
    return null;
  }

  function renderMetaText() {
    els.metaDesc.textContent = modelDescriptionText(
      describeModel(SURR_META.metadata ?? null,
        { eg: state.eg, fl: state.fl, ag: state.ag, area: state.area, pf: state.pf }));
  }

  function renderWarnings() {
    els.warnGrid.classList.toggle('hidden', !lastResult || lastInGrid);
    const sim = UI_META.sim_only_areas.includes(state.area);
    els.warnSim.classList.toggle('hidden', !sim);
    if (sim) els.simArea.textContent = String(state.area);
  }

  function renderTable(params) {
    const tds = els.paramRow.querySelectorAll('td.num');
    PARAM_LABELS.forEach(([key, digits], i) => {
      const v = params[key];
      tds[i].textContent = v == null ? '—' : v.toPrecision(digits);
    });
    els.tableWrap.classList.remove('hidden');
  }

  const LAYOUT = {
    paper_bgcolor: '#181B21',
    plot_bgcolor: '#181B21',
    font: { family: 'Inter, system-ui, sans-serif', size: 11, color: '#C4C8D0' },
    margin: { l: 56, r: 16, t: 44, b: 72 },
    showlegend: true,
    legend: { orientation: 'h', y: 1.12, x: 0, xanchor: 'left', yanchor: 'bottom' },
    hoverdistance: 30,
    xaxis: {
      gridcolor: 'rgba(95,168,211,0.10)', zeroline: false, automargin: true,
      tickangle: -35, nticks: 12, tickfont: { color: '#8A919D', size: 10 },
      title: { text: 'Frequency (GHz)' },
    },
    yaxis: {
      gridcolor: 'rgba(95,168,211,0.10)', zeroline: false, automargin: true,
      nticks: 10, tickfont: { color: '#8A919D', size: 10 },
      title: { text: '|Z11| (dB)' },
    },
  };
  const CONFIG = { responsive: true, displaylogo: false };
  let useGL = true; // scattergl 失败时降级为 SVG scatter

  function renderChart(r, truthCurve) {
    const series = [];
    if (state.showTrue && truthCurve) {
      series.push({
        x: r.freq_ghz, y: truthCurve, name: '格均值真值',
        color: '#16a34a', width: 1.6, opacity: 0.9, mode: 'lines',
      });
    }
    if (state.showBase) {
      series.push({
        x: r.freq_ghz, y: r.z_base, name: 'B0 基准',
        color: '#9ca3af', width: 1.2, mode: 'lines', dash: 'dash',
      });
    }
    series.push({
      x: r.freq_ghz, y: r.z_db, name: '代理模型',
      color: '#60a5fa', width: 2.2, mode: 'lines',
    });
    if (state.showPts) {
      series.push({
        x: r.sample_freq, y: r.sample_z, name: '稀疏点 (Stage 1)',
        color: '#ea580c', mode: 'markers',
      });
    }
    const type = useGL ? 'scattergl' : 'scatter';
    const traces = series.map((s) => ({
      type,
      mode: s.mode,
      x: s.x,
      y: s.y,
      name: s.name,
      connectgaps: false,
      line: { color: s.color, width: s.width != null ? s.width : 1.4, dash: s.dash || 'solid' },
      marker: { color: s.color, size: 5, opacity: 0.85 },
      opacity: s.opacity != null ? s.opacity : 1,
    }));
    try {
      Plotly.react(els.chart, traces, LAYOUT, CONFIG);
    } catch (e) {
      if (useGL) {
        useGL = false;
        renderChart(r, truthCurve);
      } else {
        throw e;
      }
    }
  }

  function renderAll() {
    renderMetaText();
    renderWarnings();
    if (lastResult) {
      renderTable(lastResult.params);
      renderChart(lastResult, lastTruth);
    }
  }

  // 生成频谱：去抖 + 过期请求丢弃
  let reqId = 0;
  let timer = null;

  function setLoading(on) {
    els.spinner.classList.toggle('hidden', !on);
  }

  function schedulePredict() {
    renderMetaText();
    renderWarnings();
    if (timer) clearTimeout(timer);
    const id = ++reqId;
    setLoading(true);
    timer = setTimeout(async () => {
      try {
        const r = await predict(state.eg, state.fl, state.area, state.pf, state.ag);
        if (reqId !== id) return; // 已有更新请求，丢弃过期结果
        lastResult = r;
        lastInGrid = !!findCell(UI_META.grid_cells, state.eg, state.fl, state.area, state.pf);
        const cm = CELL_MEANS && findCell(CELL_MEANS.cells, state.eg, state.fl, state.area, state.pf);
        lastTruth = cm && cm[4] && cm[4].length === r.freq_ghz.length ? cm[4] : null;
        renderWarnings();
        renderTable(r.params);
        renderChart(r, lastTruth);
      } catch (e) {
        console.error('预测失败:', e);
      } finally {
        if (reqId === id) setLoading(false);
      }
    }, DEBOUNCE_MS);
  }

  /* ---------------- 控件绑定 ---------------- */

  function syncEg(v) {
    state.eg = v;
    els.egVal.textContent = v.toFixed(2);
    els.egSlider.value = String(v);
    els.egNum.value = String(v);
    schedulePredict();
  }

  function syncFl(v) {
    state.fl = v;
    els.flVal.textContent = v.toFixed(2);
    els.flSlider.value = String(v);
    els.flNum.value = String(v);
    schedulePredict();
  }

  function initControls() {
    els.egSlider.min = String(UI_META.eg_min);
    els.egSlider.max = String(UI_META.eg_max);
    els.egNum.min = String(UI_META.eg_min);
    els.egNum.max = String(UI_META.eg_max);
    els.flSlider.min = String(UI_META.fl_min);
    els.flSlider.max = String(UI_META.fl_max);
    els.flNum.min = String(UI_META.fl_min);
    els.flNum.max = String(UI_META.fl_max);
    for (const a of UI_META.areas) {
      const opt = document.createElement('option');
      opt.value = String(a);
      opt.textContent = String(a);
      if (a === state.area) opt.selected = true;
      els.areaSel.appendChild(opt);
    }
    if (!CELL_MEANS) els.truthWrap.classList.add('hidden');

    els.egSlider.addEventListener('input', (e) => syncEg(+e.target.value));
    els.egNum.addEventListener('change', (e) => {
      const v = +e.target.value;
      if (Number.isFinite(v)) syncEg(v);
    });
    els.flSlider.addEventListener('input', (e) => syncFl(+e.target.value));
    els.flNum.addEventListener('change', (e) => {
      const v = +e.target.value;
      if (Number.isFinite(v)) syncFl(v);
    });
    els.agNum.addEventListener('change', (e) => {
      const v = e.target.value === '' ? 0 : +e.target.value;
      state.ag = Number.isFinite(v) ? v : 0;
      schedulePredict();
    });
    els.areaSel.addEventListener('change', (e) => {
      state.area = +e.target.value;
      schedulePredict();
    });
    els.pfSel.addEventListener('change', (e) => {
      state.pf = +e.target.value;
      schedulePredict();
    });
    els.showBase.addEventListener('change', (e) => {
      state.showBase = e.target.checked;
      if (lastResult) renderChart(lastResult, lastTruth);
    });
    els.showPts.addEventListener('change', (e) => {
      state.showPts = e.target.checked;
      if (lastResult) renderChart(lastResult, lastTruth);
    });
    els.showTrue.addEventListener('change', (e) => {
      state.showTrue = e.target.checked;
      if (lastResult) renderChart(lastResult, lastTruth);
    });
    syncEg(state.eg);
    syncFl(state.fl);
  }

  /* ---------------- 启动 ---------------- */

  async function boot() {
    initControls();
    try {
      await getData();
      els.loading.classList.add('hidden');
      schedulePredict();
    } catch (e) {
      els.loading.classList.add('error');
      els.loading.textContent = '代理模型加载失败：\n' + (e && e.stack ? e.stack : String(e));
      console.error(e);
    }
  }

  // 供自动化验证 / 调试使用
  window.__aln = {
    state,
    set(patch) {
      if (patch.eg != null) syncEg(patch.eg);
      if (patch.fl != null) syncFl(patch.fl);
      if (patch.ag != null) { state.ag = patch.ag; schedulePredict(); }
      if (patch.area != null) { state.area = patch.area; els.areaSel.value = String(patch.area); schedulePredict(); }
      if (patch.pf != null) { state.pf = patch.pf; els.pfSel.value = String(patch.pf); schedulePredict(); }
    },
    last() {
      return lastResult && {
        params: lastResult.params,
        z_db: lastResult.z_db,
        sample_freq: lastResult.sample_freq,
        sample_z: lastResult.sample_z,
        in_grid: lastInGrid,
        truth: lastTruth,
      };
    },
    ready: () => !!lastResult,
  };

  boot();
})();
