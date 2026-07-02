import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('electronAPI', {
  getVersion: () => ipcRenderer.invoke('app:get-version'),
  getBackendUrl: () => ipcRenderer.invoke('app:get-backend-url'),
  isBackendReady: () => ipcRenderer.invoke('backend:is-ready'),
  onBackendReady: (cb: () => void) => ipcRenderer.on('backend:ready', (_event, _value) => cb()),
  onBackendStateChange: (cb: (state: 'starting' | 'ready' | 'error') => void) =>
    ipcRenderer.on('backend:state', (_event, value) => cb(value)),
  openExternal: (url: string) => ipcRenderer.invoke('app:open-external', url),
  platform: process.platform,
});
