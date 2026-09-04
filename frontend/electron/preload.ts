import { contextBridge, ipcRenderer } from 'electron';
import type {
  AnalysisCommandMessage,
  AnalysisSurface,
} from './analysisCommands';

contextBridge.exposeInMainWorld('electronAPI', {
  getVersion: () => ipcRenderer.invoke('app:get-version'),
  getBackendUrl: () => ipcRenderer.invoke('app:get-backend-url'),
  isBackendReady: () => ipcRenderer.invoke('backend:is-ready'),
  onBackendReady: (cb: () => void) => ipcRenderer.on('backend:ready', (_event, _value) => cb()),
  onBackendStateChange: (cb: (state: 'starting' | 'ready' | 'error') => void) =>
    ipcRenderer.on('backend:state', (_event, value) => cb(value)),
  openExternal: (url: string) => ipcRenderer.invoke('app:open-external', url),
  platform: process.platform,
  checkForUpdates: () => ipcRenderer.invoke('updater:check'),
  installUpdate: () => ipcRenderer.invoke('updater:install'),
  setAnalysisContext: (active: boolean, surface: AnalysisSurface) =>
    ipcRenderer.send('analysis:set-context', { active, surface }),
  onAnalysisCommand: (cb: (message: AnalysisCommandMessage) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, message: AnalysisCommandMessage) => cb(message);
    ipcRenderer.on('analysis:command', listener);
    return () => ipcRenderer.removeListener('analysis:command', listener);
  },
  showAnalysisContextMenu: (surface: AnalysisSurface) =>
    ipcRenderer.send('analysis:show-context-menu', surface),
  writeAnalysisClipboard: (text: string) => ipcRenderer.invoke('analysis:write-clipboard', text),
});
