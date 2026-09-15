import type { AxiosResponse } from 'axios';
import api from './client';
import type { Batch, CurveSeries, Device, FieldMeta, FileEntry, Mapping, MappingEntry, PagedList, ProcessStep, ProcessStepData, ServerSyncStatus, StatsResponse, SurrogatePredictResponse, SurrogateStatus, Task } from '../types';
import { IS_STATIC } from '../config';
import * as staticApi from './static';

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

export const listBatchFiles = (batchNo: string, includeSnp = false) =>
  api
    .get(`/files?batch_no=${encodeURIComponent(batchNo)}&include_snp=${includeSnp}`)
    .then((r: AxiosResponse<FileEntry[]>) => r.data);

export const computeFile = (body: Record<string, unknown>) =>
  api.post('/files/compute', body).then((r) => r.data);

export const getFileCurve = (batchNo: string, relpath: string, param = 'z_mag_db', port = 'S11') =>
  api
    .get('/files/curve', {
      params: { batch_no: batchNo, relpath, param, port },
    })
    .then((r: AxiosResponse<{ relpath: string; port: string; freq_ghz: number[]; values: number[] }>) => r.data);

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

export const pullWebData = () => api.post('/web-sync/pull', undefined, { timeout: 0 }).then((r) => r.data as {
  mappings_added: number; batches_added: number; devices_added: number;
});

export const pushBatchToWeb = (batchNo: string, token: string) =>
  api.post(`/web-sync/push/${encodeURIComponent(batchNo)}`, { token }, { timeout: 0 }).then((r) => r.data as {
    batch_no: string; device_count: number; commit_url: string; message: string;
  });

export const getServerSyncStatus = () =>
  api.get('/desktop-sync/status').then((r: AxiosResponse<ServerSyncStatus>) => r.data);
export const getServerSyncConfig = () =>
  api.get('/desktop-sync/config').then((r) => r.data as { server_url: string | null; username: string | null });
export const saveServerSyncConfig = (serverUrl: string, username: string) =>
  api.put('/desktop-sync/config', { server_url: serverUrl, username }).then((r) => r.data);
export const pullFromServer = () =>
  api.post('/desktop-sync/pull', undefined, { timeout: 0 }).then((r) => r.data as {
    mappings_added: number; batches_added: number; devices_added: number;
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

// 虚拟文件树 API
export const listFileTree = (batchNo: string, parentId: number | string | null = null) =>
  api
    .get('/files/tree', { params: { batch_no: batchNo, parent_id: parentId } })
    .then((r) => r.data);

export const moveFileTreeNodes = (body: Record<string, unknown>) =>
  api.post('/files/tree/move', body).then((r) => r.data);

export const reorderFileTreeNodes = (body: Record<string, unknown>) =>
  api.post('/files/tree/reorder', body).then((r) => r.data);

export const mkdirFileTree = (body: Record<string, unknown>) =>
  api.post('/files/tree/mkdir', body).then((r) => r.data);

export const renameFileTreeNode = (body: Record<string, unknown>) =>
  api.post('/files/tree/rename', body).then((r) => r.data);

export const deleteFileTreeNodes = (body: Record<string, unknown>) =>
  api.post('/files/tree/delete', body).then((r) => r.data);

export const downloadFileTreeNodesZip = (batchNo: string, nodeIds: (number | string)[]) =>
  api.post(
    '/files/download-zip-nodes',
    { batch_no: batchNo, node_ids: nodeIds },
    { responseType: 'blob' }
  );

export const downloadBatchZip = (batchNo: string) =>
  api.get(`/batches/${encodeURIComponent(batchNo)}/download-zip`, {
    responseType: 'blob',
  });

export const downloadFilesZip = (batchNo: string, relpaths: string[] = []) =>
  api.post(
    '/files/download-zip',
    { batch_no: batchNo, relpaths },
    { responseType: 'blob' }
  );

export const downloadDeviceS1p = (id: number | string) =>
  api.get(`/devices/${id}/download-s1p`, {
    responseType: 'blob',
  });

const httpExportCsv = (body: Record<string, unknown>) => api.post('/export/csv', body, { responseType: 'blob' });
export const exportNnCsv = (body: Record<string, unknown>, timeoutMs: number = 0) =>
  api.post('/export/nn-csv', body, { responseType: 'blob', timeout: timeoutMs });
export const exportXlsx = (body: Record<string, unknown>) => api.post('/export/xlsx', body).then((r) => r.data);
export const downloadExport = (id: number | string) => api.get(`/exports/${id}`, { responseType: 'blob' });

export type { CurveSeries };

const httpGetSurrogateStatus = () =>
  api.get('/surrogate/status').then((r: AxiosResponse<SurrogateStatus>) => r.data);
const httpPredictSurrogate = (params: { eg: number; fl: number; area: number; pf: number; ag: number }) =>
  api.get('/surrogate/predict', { params }).then((r: AxiosResponse<SurrogatePredictResponse>) => r.data);

/* --------------------------------------------------------------------------
 * IS_STATIC 分发：静态网页版（VITE_STATIC=1）走 DuckDB-WASM/JSON/ONNX 实现，
 * 其余保持 HTTP。写操作与文件/曲线类接口在静态模式下不可达（UI 已裁剪），故不分发。
 * ------------------------------------------------------------------------ */

export const getHealth = IS_STATIC ? staticApi.getHealth : httpGetHealth;
export const getStats = IS_STATIC ? staticApi.getStats : httpGetStats;
export const listTasks = (IS_STATIC ? staticApi.listTasks : httpListTasks) as typeof httpListTasks;
export const listProcessSteps = (IS_STATIC ? staticApi.listProcessSteps : httpListProcessSteps) as typeof httpListProcessSteps;
export const getProcessStep = (IS_STATIC ? staticApi.getProcessStep : httpGetProcessStep) as typeof httpGetProcessStep;
export const listBatches = (IS_STATIC ? staticApi.listBatches : httpListBatches) as typeof httpListBatches;
export const getBatch = (IS_STATIC ? staticApi.getBatch : httpGetBatch) as typeof httpGetBatch;
export const listBatchDevices = (IS_STATIC ? staticApi.listBatchDevices : httpListBatchDevices) as typeof httpListBatchDevices;
export const listMappings = (IS_STATIC ? staticApi.listMappings : httpListMappings) as typeof httpListMappings;
export const listMappingEntries = (IS_STATIC ? staticApi.listMappingEntries : httpListMappingEntries) as typeof httpListMappingEntries;
export const queryDevices = (IS_STATIC ? staticApi.queryDevices : httpQueryDevices) as typeof httpQueryDevices;
export const queryAggregate = (IS_STATIC ? staticApi.queryAggregate : httpQueryAggregate) as typeof httpQueryAggregate;
export const getQueryFields = (IS_STATIC ? staticApi.getQueryFields : httpGetQueryFields) as typeof httpGetQueryFields;
export const getQueryDistinct = (IS_STATIC ? staticApi.getQueryDistinct : httpGetQueryDistinct) as typeof httpGetQueryDistinct;
// Alias commonly used by UI components
export const distinctValues = (field: string, limit = 500) => getQueryDistinct(field, limit);
export const exportCsv = (IS_STATIC ? staticApi.exportCsv : httpExportCsv) as unknown as typeof httpExportCsv;
export const getSurrogateStatus = (IS_STATIC ? staticApi.getSurrogateStatus : httpGetSurrogateStatus) as typeof httpGetSurrogateStatus;
export const predictSurrogate = (IS_STATIC ? staticApi.predictSurrogate : httpPredictSurrogate) as typeof httpPredictSurrogate;
