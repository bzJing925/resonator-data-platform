import type { AxiosResponse } from 'axios';
import api from './client';
import type { Batch, CurveSeries, Device, FieldMeta, FileEntry, Mapping, MappingEntry, PagedList, StatsResponse, Task } from '../types';

export const getHealth = () => api.get('/health').then((r) => r.data);
export const getStats = () => api.get('/stats').then((r: AxiosResponse<StatsResponse>) => r.data);

export const listBatches = (params?: Record<string, unknown>) =>
  api.get('/batches', { params }).then((r: AxiosResponse<PagedList<Batch> | { items: Batch[]; total: number }>) => r.data);
export const getBatch = (batchNo: string) =>
  api.get(`/batches/${encodeURIComponent(batchNo)}`).then((r: AxiosResponse<Batch>) => r.data);
export const deleteBatch = (batchNo: string) =>
  api.delete(`/batches/${encodeURIComponent(batchNo)}`).then((r) => r.data);
export const listBatchDevices = (batchNo: string, params?: Record<string, unknown>) =>
  api
    .get(`/batches/${encodeURIComponent(batchNo)}/devices`, { params })
    .then((r: AxiosResponse<PagedList<Device>>) => r.data);

export const listBatchFiles = (batchNo: string, includeSnp = false) =>
  api
    .get(`/files?batch_no=${encodeURIComponent(batchNo)}&include_snp=${includeSnp}`)
    .then((r: AxiosResponse<FileEntry[]>) => r.data);

export const computeFile = (body: Record<string, unknown>) =>
  api.post('/files/compute', body).then((r) => r.data);

export const getFileCurve = (batchNo: string, relpath: string, param = 'z_mag_db') =>
  api
    .get('/files/curve', {
      params: { batch_no: batchNo, relpath, param },
    })
    .then((r: AxiosResponse<{ relpath: string; freq_ghz: number[]; values: number[] }>) => r.data);

export const listMappings = () => api.get('/mappings').then((r: AxiosResponse<Mapping[] | PagedList<Mapping>>) => r.data);
export const createMapping = (formData: FormData) =>
  api
    .post('/mappings', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    .then((r) => r.data);
export const listMappingEntries = (id: number | string, params?: Record<string, unknown>) =>
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

export const listTasks = (params?: Record<string, unknown>) =>
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

export const queryDevices = (body: Record<string, unknown>) =>
  api.post('/query/devices', body).then((r) => r.data);
export const queryAggregate = (body: Record<string, unknown>) =>
  api.post('/query/aggregate', body).then((r) => r.data);
export const getQueryFields = () => api.get('/query/fields').then((r: AxiosResponse<Record<string, FieldMeta[]>>) => r.data);
export const getQueryDistinct = (field: string, limit = 500) =>
  api.get('/query/distinct', { params: { field, limit } }).then((r: AxiosResponse<unknown[] | { values: unknown[] }>) => r.data);
// Alias commonly used by UI components
export const distinctValues = (field: string, limit = 500) => getQueryDistinct(field, limit);

export const getDeviceSparam = (id: number | string, param = 's11_db') =>
  api
    .get(`/devices/${id}/sparam`, { params: { param } })
    .then((r) => r.data);
export const getDeviceBodeq = (id: number | string) =>
  api.get(`/devices/${id}/bodeq`).then((r) => r.data);

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

export const exportCsv = (body: Record<string, unknown>) => api.post('/export/csv', body, { responseType: 'blob' });
export const exportXlsx = (body: Record<string, unknown>) => api.post('/export/xlsx', body).then((r) => r.data);
export const downloadExport = (id: number | string) => api.get(`/exports/${id}`, { responseType: 'blob' });

export type { CurveSeries };
