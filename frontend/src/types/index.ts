export interface ElectronAPI {
  getVersion: () => Promise<string>;
  getBackendUrl: () => Promise<string>;
  isBackendReady: () => Promise<boolean>;
  onBackendReady: (cb: () => void) => void;
  onBackendStateChange: (cb: (state: 'starting' | 'ready' | 'error') => void) => void;
  openExternal: (url: string) => Promise<void>;
  platform: string;
  checkForUpdates?: () => Promise<{ version: string; available: boolean }>;
  installUpdate?: () => Promise<void>;
}

declare global {
  interface Window {
    electronAPI?: ElectronAPI;
  }
}

export interface SSEProgressEvent {
  progress_pct?: number;
  stage_progress_pct?: number;
  stage?: string;
  progress_msg?: string;
  status?: string;
  error_msg?: string;
}
