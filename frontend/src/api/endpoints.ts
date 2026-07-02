import api from './client';

export const getHealth = () => api.get('/health').then((r) => r.data);
export const getStats = () => api.get('/stats').then((r) => r.data);

export const listBatches = (params) =>
  api.get('/batches', { params }).then((r) => r.data);
export const getBatch = (batchNo) =>
  api.get(`/batches/${encodeURIComponent(batchNo)}`).then((r) => r.data);
export const deleteBatch = (batchNo) =>
  api.delete(`/batches/${encodeURIComponent(batchNo)}`).then((r) => r.data);
export const listBatchDevices = (batchNo, params) =>
  api
    .get(`/batches/${encodeURIComponent(batchNo)}/devices`, { params })
    .then((r) => r.data);

export const listBatchFiles = (batchNo, includeSnp = false) =>
  api
    .get(`/files?batch_no=${encodeURIComponent(batchNo)}&include_snp=${includeSnp}`)
    .then((r) => r.data);

export const computeFile = (body) =>
  api.post('/files/compute', body).then((r) => r.data);

export const getFileCurve = (batchNo, relpath, param = 'z_mag_db') =>
  api
    .get('/files/curve', {
      params: { batch_no: batchNo, relpath, param },
    })
    .then((r) => r.data);

export const listMappings = () => api.get('/mappings').then((r) => r.data);
export const createMapping = (formData) =>
  api
    .post('/mappings', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    .then((r) => r.data);
export const listMappingEntries = (id, params) =>
  api.get(`/mappings/${id}/entries`, { params }).then((r) => r.data);
export const deleteMapping = (id) =>
  api.delete(`/mappings/${id}`).then((r) => r.data);

export const uploadBatch = (formData, onProgress) =>
  api
    .post('/uploads', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 0,
      onUploadProgress: onProgress,
    })
    .then((r) => r.data);

export const listTasks = (params) =>
  api.get('/tasks', { params }).then((r) => r.data);
export const getTask = (taskId) =>
  api.get(`/tasks/${taskId}`).then((r) => r.data);

export const queryDevices = (body) =>
  api.post('/query/devices', body).then((r) => r.data);
export const queryAggregate = (body) =>
  api.post('/query/aggregate', body).then((r) => r.data);
export const getQueryFields = () =>
  api.get('/query/fields').then((r) => r.data);
export const getQueryDistinct = (field, limit = 500) =>
  api.get('/query/distinct', { params: { field, limit } }).then((r) => r.data);
// Alias commonly used by UI components
export const distinctValues = (field, limit = 500) => getQueryDistinct(field, limit);

export const getDeviceSparam = (id, param = 's11_db') =>
  api
    .get(`/devices/${id}/sparam`, { params: { param } })
    .then((r) => r.data);
export const getDeviceBodeq = (id) =>
  api.get(`/devices/${id}/bodeq`).then((r) => r.data);

export const getDeviceSparseSparam = (id, param = 'z_mag_db', piezo = '308', nPoints = 300) =>
  api
    .get(`/devices/${id}/sparam-sparse`, { params: { param, piezo, n_points: nPoints } })
    .then((r) => r.data);

export const downloadBatchZip = (batchNo) =>
  api.get(`/batches/${encodeURIComponent(batchNo)}/download-zip`, {
    responseType: 'blob',
  });

export const downloadFilesZip = (batchNo, relpaths = []) =>
  api.post(
    '/files/download-zip',
    { batch_no: batchNo, relpaths },
    { responseType: 'blob' }
  );

export const downloadDeviceS1p = (id) =>
  api.get(`/devices/${id}/download-s1p`, {
    responseType: 'blob',
  });

export const exportCsv = (body) =>
  api.post('/export/csv', body, { responseType: 'blob' });
export const exportXlsx = (body) =>
  api.post('/export/xlsx', body).then((r) => r.data);
export const downloadExport = (id) =>
  api.get(`/exports/${id}`, { responseType: 'blob' });
