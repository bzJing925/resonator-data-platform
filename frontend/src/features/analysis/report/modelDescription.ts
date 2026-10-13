export interface ModelDescription {
  schema_version: 1; model_digest: string | null; training_range: Record<string, [number, number]> | null;
  training_source: 'measured' | 'simulation' | 'mixed' | 'unknown';
  validation_status: 'verified' | 'unverified' | 'unknown'; validation_metrics: Record<string, number | null> | null;
  validation_dataset_digest: string | null; applicability: 'within_range' | 'out_of_range' | 'unknown';
}
export function describeModel(value: unknown, inputs?: Record<string, number>): ModelDescription {
  const unknown: ModelDescription = { schema_version: 1, model_digest: null, training_range: null, training_source: 'unknown', validation_status: 'unknown', validation_metrics: null, validation_dataset_digest: null, applicability: 'unknown' };
  if (!value || typeof value !== 'object' || Array.isArray(value)) return unknown;
  const v = value as Record<string, any>;
  if (v.schema_version !== 1) return unknown;
  const digest = (x: unknown): string | null => typeof x === 'string' && /^[a-f0-9]{64}$/i.test(x) ? x : null;
  const range = v.training_range_status === 'known' && v.training_range && typeof v.training_range === 'object'
    && Object.values(v.training_range).every((bounds: any) => Array.isArray(bounds) && bounds.length === 2 && bounds.every(Number.isFinite) && bounds[0] <= bounds[1]) ? structuredClone(v.training_range) : null;
  const metrics = v.validation_metrics && typeof v.validation_metrics === 'object' && !Array.isArray(v.validation_metrics)
    && Object.values(v.validation_metrics).every((x) => x === null || typeof x === 'number' && Number.isFinite(x)) ? structuredClone(v.validation_metrics) : null;
  const model = { ...unknown, model_digest: digest(v.model_digest), training_range: range,
    training_source: ['measured', 'simulation', 'mixed'].includes(v.training_source) ? v.training_source : 'unknown',
    validation_status: ['verified', 'unverified'].includes(v.validation_status) ? v.validation_status : 'unknown',
    validation_metrics: metrics, validation_dataset_digest: digest(v.validation_dataset_digest) };
  if (model.validation_status === 'verified' && (!model.model_digest || !model.validation_dataset_digest || !metrics)) model.validation_status = 'unverified';
  if (inputs && range && Object.keys(range).length) {
    const entries = Object.entries(range) as [string, [number, number]][];
    model.applicability = entries.some(([key, [low, high]]) => Number.isFinite(inputs[key]) && (inputs[key] < low || inputs[key] > high)) ? 'out_of_range'
      : entries.every(([key]) => Number.isFinite(inputs[key])) ? 'within_range' : 'unknown';
  } else if (['within_range', 'out_of_range'].includes(v.applicability)) model.applicability = range ? v.applicability : 'unknown';
  return model;
}
export function modelDescriptionText(model: ModelDescription) {
  return `模型版本：${model.model_digest ?? '未知'}；训练范围：${model.training_range ? JSON.stringify(model.training_range) : '未知/未提供'}；训练数据：${model.training_source}；验证：${model.validation_status}；验证指标：${model.validation_metrics ? JSON.stringify(model.validation_metrics) : '未知/未提供'}；适用性：${model.applicability}。未提供预测置信区间。`;
}
