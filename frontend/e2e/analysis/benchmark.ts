import Plotly from 'plotly.js-dist-min';
import { buildJmpChartScene } from '../../src/features/analysis/chart/chartScene';
import {
  buildCachedJmpChartScene,
  clearJmpChartSceneCache,
} from '../../src/features/analysis/chart/chartSceneCache';
import { datumRowIds } from '../../src/features/analysis/chart/chartInteraction';
import {
  ANALYSIS_Y_FIELDS,
  buildPerformanceRows,
} from '../../src/features/analysis/__fixtures__/analysisRows';

interface BenchmarkResult {
  rowCount: number;
  emittedPointCount: number;
  mappedPointCount: number;
  sceneMs: number;
  cacheHitMs: number;
  renderMs: number;
  zoomMs: number;
  selectMs: number;
  refreshMs: number;
  batchDeleteMs: number;
  panFps: number;
}

declare global {
  interface Window {
    runAnalysisBenchmark: (rowCount: number) => Promise<BenchmarkResult>;
  }
}

const graph = document.getElementById('plot') as HTMLElement;

async function timed(operation: () => unknown | Promise<unknown>) {
  const started = performance.now();
  await operation();
  return performance.now() - started;
}

async function measurePanFps(graphDiv: any) {
  return new Promise<number>((resolve) => {
    const started = performance.now();
    let frames = 0;
    const step = async (now: number) => {
      frames += 1;
      const shift = (frames % 20) * 0.005;
      await Plotly.relayout(graphDiv, { 'xaxis.range': [-0.5 + shift, 1.5 + shift] });
      if (now - started < 1_000) requestAnimationFrame(step);
      else resolve(frames * 1000 / (performance.now() - started));
    };
    requestAnimationFrame(step);
  });
}

window.runAnalysisBenchmark = async (rowCount) => {
  const rows = buildPerformanceRows(rowCount);
  const sceneStarted = performance.now();
  const spec = buildJmpChartScene({
    chartType: 'scatter',
    rows,
    yFields: ANALYSIS_Y_FIELDS,
    datasetRevision: 1,
  });
  const sceneMs = performance.now() - sceneStarted;
  const emittedPointCount = spec.allTraces.reduce(
    (total, trace) => total + (Array.isArray(trace.x) ? trace.x.length : 0),
    0,
  );
  const mappedPointCount = spec.allTraces.reduce(
    (total, trace) => total + (Array.isArray(trace.customdata)
      ? trace.customdata.reduce(
          (traceTotal: number, datum: unknown) => traceTotal + datumRowIds(datum).length,
          0,
        )
      : 0),
    0,
  );
  clearJmpChartSceneCache();
  const cachedArgs = {
    chartType: 'scatter',
    rows,
    yFields: ANALYSIS_Y_FIELDS,
    datasetRevision: 1,
  };
  buildCachedJmpChartScene(`benchmark-${rowCount}`, cachedArgs);
  const cacheHitMs = await timed(() => {
    buildCachedJmpChartScene(`benchmark-${rowCount}`, cachedArgs);
  });
  const layout = { ...spec.layout, width: 1280, height: 800, uirevision: `benchmark-${rowCount}` };
  const renderMs = await timed(() => Plotly.react(graph, spec.allTraces, layout, {
    displaylogo: false,
    responsive: false,
  }));
  const graphDiv = graph as any;
  const zoomMs = await timed(() => Plotly.relayout(graphDiv, { 'xaxis.range': [-0.25, 1.25] }));
  const traceIndices = graphDiv.data.map((_trace: unknown, index: number) => index);
  const selectedpoints = graphDiv.data.map((trace: any) =>
    trace.customdata?.length ? [0, Math.floor(trace.customdata.length / 2)] : []);
  const selectMs = await timed(() => Plotly.restyle(graphDiv, { selectedpoints }, traceIndices));
  const refreshMs = await timed(() => Plotly.react(graphDiv, spec.allTraces, layout, {
    displaylogo: false,
    responsive: false,
  }));
  const deleteCount = Math.max(100, Math.floor(rowCount * 0.01));
  const batchDeleteMs = await timed(async () => {
    const deletedSpec = buildJmpChartScene({
      chartType: 'scatter',
      rows: rows.slice(deleteCount),
      yFields: ANALYSIS_Y_FIELDS,
      datasetRevision: 2,
    });
    await Plotly.react(graphDiv, deletedSpec.allTraces, {
      ...deletedSpec.layout,
      width: 1280,
      height: 800,
      uirevision: `benchmark-${rowCount}`,
    }, {
      displaylogo: false,
      responsive: false,
    });
  });
  const panFps = await measurePanFps(graphDiv);
  return {
    rowCount,
    emittedPointCount,
    mappedPointCount,
    sceneMs,
    cacheHitMs,
    renderMs,
    zoomMs,
    selectMs,
    refreshMs,
    batchDeleteMs,
    panFps,
  };
};

document.body.dataset.ready = 'true';
