import { buildJmpChartScene } from './chartScene';
import type { ChartScene, ChartSceneArgs } from './types';

const MAX_CACHED_SCENES = 8;

interface CacheEntry {
  rows: Record<string, unknown>[];
  scene: ChartScene;
}

const sceneCache = new Map<string, CacheEntry>();

function fieldSignature(args: ChartSceneArgs) {
  return args.yFields.map((field) => [
    field.name,
    field.label || '',
    field.unit || '',
    !!field.isCategorical,
  ]);
}

function sceneKey(datasetKey: string, args: ChartSceneArgs) {
  return JSON.stringify([
    datasetKey,
    args.datasetRevision,
    args.chartType,
    ['structureType', 'areaReality'],
    fieldSignature(args),
    args.height ?? 250,
    args.width ?? null,
  ]);
}

export function buildCachedJmpChartScene(
  datasetKey: string | undefined,
  args: ChartSceneArgs,
): ChartScene {
  if (!datasetKey || args.datasetRevision === undefined) return buildJmpChartScene(args);

  const key = sceneKey(datasetKey, args);
  const cached = sceneCache.get(key);
  if (cached?.rows === args.rows) {
    sceneCache.delete(key);
    sceneCache.set(key, cached);
    return cached.scene;
  }

  const scene = buildJmpChartScene(args);
  sceneCache.set(key, { rows: args.rows, scene });
  while (sceneCache.size > MAX_CACHED_SCENES) {
    const oldestKey = sceneCache.keys().next().value;
    if (oldestKey === undefined) break;
    sceneCache.delete(oldestKey);
  }
  return scene;
}

export function clearJmpChartSceneCache() {
  sceneCache.clear();
}
