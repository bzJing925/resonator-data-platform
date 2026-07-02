import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('electronAPI', {
  getVersion: () => ipcRenderer.invoke('app:get-version'),
  getBackendUrl: () => ipcRenderer.invoke('app:get-backend-url'),
  isBackendReady: () => ipcRenderer.invoke('backend:is-ready'),
  onBackendReady: (cb: () => void) => ipcRenderer.on('backend:ready', (_event, _value) => cb()),
  openExternal: (url: string) => ipcRenderer.invoke('app:open-external', url),
  platform: process.platform,
});
