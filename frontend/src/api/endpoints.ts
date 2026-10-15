import type { AxiosResponse } from 'axios';
import api from './client';
import type { Batch, Device, FieldMeta, Mapping, MappingEntry, PagedList, ProcessStep, ProcessStepData, ServerSyncStatus, StatsResponse, Task } from '../types';

const httpGetHealth = () => api.get('/health').then((r) => r.data);
const httpGetStats = () => api.get('/stats').then((r: AxiosResponse<StatsResponse>) => r.data);

const httpListProcessSteps = () =>
  api.get('/process/steps').then((r: AxiosResponse<ProcessStep[]>) => r.data);
const httpGetProcessStep = (step: string) =>
  api.get(`/process/${step}`).then((r: AxiosResponse<ProcessStepData>) => r.data);

const httpListBatches = (params?: Record<string, unknown>) =>
  api.get('/batches', { params }).then((r: AxiosResponse<PagedList<Batch> | { items: Batch[]; total: number }>) => r.data);
const httpGetBatch = (batchNo: string) =>
  api.get(`/batches/${encodeURIComponent(batchNo)}`).then((r: AxiosResponse<Batch>) => r.data);
export const deleteBatch = (batchNo: string) =>
  api.delete(`/batches/${encodeURIComponent(batchNo)}`).then((r) => r.data);
export const renameBatch = (batchNo: string, newBatchNo: string) =>
  api
    .post(`/batches/${encodeURIComponent(batchNo)}/rename`, { new_batch_no: newBatchNo })
    .then((r) => r.data as { batch_no: string });
const httpListBatchDevices = (batchNo: string, params?: Record<string, unknown>) =>
  api
    .get(`/batches/${encodeURIComponent(batchNo)}/devices`, { params })
    .then((r: AxiosResponse<PagedList<Device>>) => r.data);

const httpListMappings = () => api.get('/mappings').then((r: AxiosResponse<Mapping[] | PagedList<Mapping>>) => r.data);
export const createMapping = (formData: FormData) =>
  api
    .post('/mappings', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    .then((r) => r.data);
const httpListMappingEntries = (id: number | string, params?: Record<string, unknown>) =>
  api.get(`/mappings/${id}/entries`, { params }).then((r: AxiosResponse<PagedList<MappingEntry>>) => r.data);
export const deleteMapping = (id: number | string) =>
  api.delete(`/mappings/${id}`).then((r) => r.data);

export const uploadBatch = (formData: FormData, onProgress?: (e: { loaded: number; total?: number }) => void) =>
  api
    .post('/uploads', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 0,
      onUploadProgress: onProgress,
    })
    .then((r) => r.data);

export const getServerSyncStatus = () =>
  api.get('/desktop-sync/status').then((r: AxiosResponse<ServerSyncStatus>) => r.data);
export const saveServerSyncConfig = (serverUrl: string, username: string) =>
  api.put('/desktop-sync/config', { server_url: serverUrl, username }).then((r) => r.data);
export const pullFromServer = () =>
  api.post('/desktop-sync/synchronize', undefined, { timeout: 0 }).then((r) => r.data as {
    mappings_added: number; batches_added: number; devices_added: number;
    batches_updated?: number; batches_deleted?: number; tables_pushed?: number;
    mappings_pushed?: number; raw_pushed?: number; errors?: string[];
  });
export const pushBatchToServer = (batchNo: string) =>
  api.post(`/desktop-sync/push/${encodeURIComponent(batchNo)}`, undefined, { timeout: 0 }).then((r) => r.data as {
    status: string; device_count?: number;
  });

const httpListTasks = (params?: Record<string, unknown>) =>
  api.get('/tasks', { params }).then((r: AxiosResponse<Task[] | PagedList<Task>>) => r.data);
export const getTask = (taskId: number | string) =>
  api.get(`/tasks/${taskId}`).then((r: AxiosResponse<Task>) => r.data);
export const cancelTask = (taskId: number | string) =>
  api.post(`/tasks/${taskId}/cancel`).then((r: AxiosResponse<Task>) => r.data);

export const reextractBatch = (batchNo: string) =>
  api.post(`/batches/${encodeURIComponent(batchNo)}/reextract`).then((r) => r.data);
export const redeembedBatch = (batchNo: string) =>
  api.post(`/batches/${encodeURIComponent(batchNo)}/redeembed`).then((r) => r.data);
export const recomputeBatch = (batchNo: string, metrics: string[]) =>
  api.post(`/batches/${encodeURIComponent(batchNo)}/recompute`, { metrics }).then((r) => r.data);

const httpQueryDevices = (body: Record<string, unknown>) =>
  api.post('/query/devices', body).then((r) => r.data);
const httpQueryAggregate = (body: Record<string, unknown>) =>
  api.post('/query/aggregate', body).then((r) => r.data);
const httpGetQueryFields = () => api.get('/query/fields').then((r: AxiosResponse<Record<string, FieldMeta[]>>) => r.data);
const httpGetQueryDistinct = (field: string, limit = 500) =>
  api.get('/query/distinct', { params: { field, limit } }).then((r: AxiosResponse<unknown[] | { values: unknown[] }>) => r.data);

export const getDeviceSparam = (id: number | string, param = 's11_db') =>
  api
    .get(`/devices/${id}/sparam`, { params: { param } })
    .then((r) => r.data);
export const getDeviceBodeq = (id: number | string) =>
  api
    .get(`/devices/${id}/bodeq`).then((r) => r.data);

export const getDeviceSparseSparam = (id: number | string, param = 'z_mag_db', piezo = '308', nPoints = 300) =>
  api
    .get(`/devices/${id}/sparam-sparse`, { params: { param, piezo, n_points: nPoints } })
    .then((r) => r.data);

export const downloadDeviceS1p = (id: number | string) =>
  api.get(`/devices/${id}/download-s1p`, {
    responseType: 'blob',
  });

const httpExportCsv = (body: Record<string, unknown>) => api.post('/export/csv', body, { responseType: 'blob' });

export const getHealth = httpGetHealth;
export const getStats = httpGetStats;
export const listTasks = httpListTasks;
export const listProcessSteps = httpListProcessSteps;
export const getProcessStep = httpGetProcessStep;
export const listBatches = httpListBatches;
export const getBatch = httpGetBatch;
export const listBatchDevices = httpListBatchDevices;
export const listMappings = httpListMappings;
export const listMappingEntries = httpListMappingEntries;
export const queryDevices = httpQueryDevices;
export const queryAggregate = httpQueryAggregate;
export const getQueryFields = httpGetQueryFields;
const getQueryDistinct = httpGetQueryDistinct;
// Alias commonly used by UI components
export const distinctValues = (field: string, limit = 500) => getQueryDistinct(field, limit);
export const exportCsv = httpExportCsv;

// Durable full-filter export.
export function createExportJob(body: { request_key: string; format: 'csv' | 'xlsx'; query_spec: import('../types/platformContracts').QuerySpec; expected_sources: import('../types/platformContracts').SourceRef[] }) {
  return api.post('/export/jobs', body).then((r) => r.data);
}
export function getExportJob(id: string) {
  return api.get(`/export/jobs/${encodeURIComponent(id)}`).then((r) => r.data);
}
export function inspectComparisonConditions(body: { comparison: import('../types/platformContracts').AnalysisComparison; quality_rule: import('../types/platformContracts').QualityRuleV1 | null }) {
  return api.post('/analysis/compare/conditions', body).then((r) => r.data);
}

export const prepareUploadDraft = (body: FormData, onProgress?: (value: { loaded: number; total?: number }) => void) =>
  api.post('/uploads/prepare', body, { timeout: 0, onUploadProgress: onProgress }).then((response) => response.data as { draft_id: string });
export const getUploadGroup = (groupId: string) => api.get(`/uploads/groups/${groupId}`).then((response) => response.data as { group_id: string; drafts: import('../upload/types').UploadDraft[] });
export const commitUploadDraft = (id: string, body: { request_key: string; check_digest: string; acknowledged_warning_codes: string[] }) => api.post(`/uploads/drafts/${id}/commit`, body).then((response) => response.data as { task_id: string; run_id: string; batch_no: string; status: string });
export const recheckUploadDraft = (id: string, body: import('../upload/types').UploadOptions) => api.post(`/uploads/drafts/${id}/recheck`, body).then((response) => response.data);
export const getProcessingReport = (id: string | number, offset = 0) => api.get(`/tasks/${id}/report`, { params: { offset, limit: 100 } }).then((response) => response.data as import('../upload/types').ProcessingReport);
export const downloadProcessingIssues = (id: string | number) => api.get(`/tasks/${id}/issues.csv`, { responseType: 'blob' });

export interface RetryOptions { task_id: number; run_id: string | null; expected_source_revision: number | null; retry_of_task_id: number | null; total: number; items: { id: string; unit_kind: string; source_member: string; port: string | null; status: string; error_code: string | null }[]; next_offset: number | null }
export const getRetryOptions = (id: string | number, offset = 0) => api.get(`/tasks/${id}/retry-options`, { params: { offset, limit: 100 } }).then((r) => r.data as RetryOptions);
export const retryTask = (id: string | number, body: { request_key: string; mode: 'failed'; item_ids?: string[] | null; expected_source_revision: number }) => api.post(`/tasks/${id}/retry`, body).then((r) => r.data as { task_id: string; run_id: string; batch_no: string; status: string });
export interface DeletionImpact { batch_no: string; source_ref: import('../types/platformContracts').SourceRef; device_count: number; scope: string; raw_input_available: boolean; active_task_id: number | null; purge_after: string }
export const getDeletionImpact = (name: string) => api.get(`/batches/${encodeURIComponent(name)}/deletion-impact`).then((r) => r.data as DeletionImpact);
export const hideReplica = (source_ref: import('../types/platformContracts').SourceRef, hidden: boolean) => api.post('/replica-preferences', { source_ref, hidden }).then((r) => r.data);
export const getReplicaPreferences = () => api.get('/replica-preferences').then((r) => r.data as { items: { batch_no: string | null; source_ref: import('../types/platformContracts').SourceRef; hidden: boolean }[] });
export const deleteRemoteSource = (source: import('../types/platformContracts').SourceRef) => api.delete(`/sync/v3/sources/${source.origin_id}/${source.source_batch_id}`, { data: { expected_revision: source.source_revision } }).then((r) => r.data);
export const getTrash = (page = 1) => api.get('/trash', { params: { page, size: 20 } }).then((r) => r.data as { total: number; items: DeletionImpact[] });
export const restoreBatch = (name: string) => api.post(`/batches/${encodeURIComponent(name)}/restore`).then((r) => r.data);

export const getRemoteDeletionImpact = (source: import('../types/platformContracts').SourceRef) => api.get(`/sync/v3/sources/${source.origin_id}/${source.source_batch_id}/deletion-impact`).then((r) => r.data as DeletionImpact);
export const cancelExportJob = (id: string) => api.post(`/export/jobs/${id}/cancel`).then((r) => r.data);
export const downloadExportArtifact = (id: string, name: string) => api.get(`/export/jobs/${id}/artifacts/${encodeURIComponent(name)}`, { responseType: 'blob' }).then((r) => r.data as Blob);
