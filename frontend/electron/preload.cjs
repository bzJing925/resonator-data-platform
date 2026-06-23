const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  getVersion: () => ipcRenderer.invoke('app:get-version'),
  getBackendUrl: () => ipcRenderer.invoke('app:get-backend-url'),
  isBackendReady: () => ipcRenderer.invoke('backend:is-ready'),
  onBackendReady: (cb) => ipcRenderer.on('backend:ready', (_event, value) => cb(value)),
  openExternal: (url) => ipcRenderer.invoke('app:open-external', url),
  platform: process.platform,
});
