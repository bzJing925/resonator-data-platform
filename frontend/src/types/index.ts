import type {
  AnalysisCommandMessage,
  AnalysisSurface,
} from '../../electron/analysisCommands';

export interface ElectronAPI {
  getVersion: () => Promise<string>;
  getBackendUrl: () => Promise<string>;
  isBackendReady: () => Promise<boolean>;
  onBackendReady: (cb: () => void) => (() => void) | void;
  onBackendStateChange: (cb: (state: 'starting' | 'ready' | 'error') => void) => void;
  openExternal: (url: string) => Promise<void>;
  platform: string;
  checkForUpdates?: () => Promise<{ version: string; available: boolean }>;
  installUpdate?: () => Promise<void>;
  setAnalysisContext?: (active: boolean, surface: AnalysisSurface) => void;
  onAnalysisCommand?: (cb: (message: AnalysisCommandMessage) => void) => (() => void) | void;
  showAnalysisContextMenu?: (surface: AnalysisSurface) => void;
  writeAnalysisClipboard?: (text: string) => Promise<void>;
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

/* --------------------------------------------------------------------------
 * Domain types used across the frontend.
 * Many API payloads are treated as loosely-typed records; these interfaces
 * capture the fields the UI actually depends on.
 * ------------------------------------------------------------------------ */

export interface FieldMeta {
  name: string;
  label?: string;
  unit?: string;
  section?: 'categorical' | 'process' | 'geometric' | 'numeric' | string;
  values?: unknown[];
  values_endpoint?: string;
}

export interface FieldsData {
  raw?: Record<string, FieldMeta[]>;
  all?: FieldMeta[];
  byName?: Record<string, FieldMeta>;
  numeric?: FieldMeta[];
  categorical?: FieldMeta[];
  process?: FieldMeta[];
  geometric?: FieldMeta[];
}

export interface Batch {
  batch_no: string;
  mapping_name?: string;
  device_count?: number;
  f_start_ghz?: number;
  f_end_ghz?: number;
  deembedded?: boolean;
  process_type?: string;
  uploaded_at?: string;
  wafers?: (string | number)[];
  stats?: {
    fs_ghz_median?: number;
    pass_rate?: number;
  };
  raw_zip_deleted?: boolean;
  task_id?: number | string;
  synced_at?: string | null;
  source?: string;
}

export interface Task {
  id: number | string;
  batch_no?: string;
  status?: string;
  progress_pct?: number;
  progress_msg?: string;
  error_msg?: string;
  started_at?: string;
  finished_at?: string;
  cancelled_at?: string;
  stage?: string;
  stage_progress_pct?: number;
  raw_zip_deleted?: boolean;
}

export interface Device {
  id?: number | string;
  batch_no?: string;
  original_filename?: string;
  display_name?: string;
  mark?: string;
  description?: string;
  wafer?: string | number;
  folder_name?: string;
  coord?: string;
  x?: number;
  y?: number;
  eg?: number;
  fl?: number;
  ag?: number;
  pf?: string;
  area_n?: number;
  area_um2?: number;
  fs_ghz?: number;
  fp_ghz?: number;
  zs_ohm?: number;
  zp_ohm?: number;
  qs?: number;
  qp?: number;
  qs_bodeq?: number;
  qp_bodeq?: number;
  dbqs?: number;
  dbqp?: number;
  bodeq_fitted?: unknown;
  bodeq_smooth?: unknown;
  bodeq_raw?: unknown;
  fbode_ghz?: number;
  k2eff_pct?: number;
  fp2_ghz?: number;
  fs2_ghz?: number;
  zp2_ohm?: number;
  zs2_ohm?: number;
  deembedded?: boolean;
  s_param_path?: string;
  s_param_port?: string;
  [key: string]: unknown;
}

export interface Mapping {
  id: number | string;
  name?: string;
  entry_count?: number;
  in_use_by_batches?: number;
  uploaded_at?: string;
}

export interface MappingEntry {
  mark?: string;
  description?: string;
  eg?: number;
  fl?: number;
  ag?: number;
  area_s11?: number;
  area_s22?: number;
  has_pf?: boolean;
}

export interface FileEntry {
  name: string;
  relpath: string;
  size: number;
  computed?: boolean;
}


export interface ChartField {
  name: string;
  label?: string;
  unit?: string;
  section?: string;
  isCategorical?: boolean;
}

export interface PagedList<T> {
  items: T[];
  total: number;
  page?: number;
  size?: number;
}

export interface StatsResponse {
  batches?: number;
  devices?: number;
  mappings?: number;
  disk_used_gb?: number;
  disk_free_gb?: number;
  tasks_running?: number;
  tasks_pending?: number;
}

export interface ProcessFieldMeta {
  name: string;
  label: string;
  unit?: string | null;
  kind: 'numeric' | 'text';
}

export interface ProcessStep {
  key: string;
  label: string;
  row_count: number;
}

export interface ProcessStepData {
  fields: ProcessFieldMeta[];
  rows: Record<string, string | number | null>[];
}

export interface SurrogateStatus {
  available: boolean;
  reason?: string;
  eg_min?: number;
  eg_max?: number;
  fl_min?: number;
  fl_max?: number;
  areas?: number[];
  /** 仅由校准仿真伪标签支撑、无实测验证的面积档（前端应显示"仿真外推"注释） */
  sim_only_areas?: number[];
}

export interface SurrogatePredictResponse {
  params: Record<string, number>;
  freq: number[];
  z: number[];
  z_base: number[];
  sample_freq: number[];
  sample_z: number[];
  in_grid: boolean;
  true_curves?: number[][];
  true_mean?: Record<string, number>;
}

export interface ServerSyncStatus {
  enabled: boolean;
  configured: boolean;
  server_url: string | null;
  username: string | null;
  reachable: boolean;
  unsynced_batches: string[];
}
