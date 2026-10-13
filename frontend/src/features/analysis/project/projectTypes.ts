import type { AnalysisDataset, DatasetRegistry } from '../model/dataset';
import type { PointStyleState } from '../model/commands';
import type { AnalysisSpec, ResultManifest, SourceRef } from '../../../types/platformContracts';
import type { ExcludedRow, JsonValue, LocalEdit } from '../model/provenance';

export type { JsonValue, LocalEdit, ExcludedRow };
export type ProjectKind = 'draft' | 'named' | 'snapshot';
export type RegistryMetadata = Omit<DatasetRegistry, 'byId'> & {
  byId: Record<string, Omit<AnalysisDataset, 'rows'>>;
};
export interface ProjectHead {
  projectId: string;
  currentVersionId: string;
  generation: number;
  name: string;
  kind: ProjectKind;
  updatedAt: string;
  lastOpenedAt: string;
  schemaVersion: 1;
}
export interface ProjectVersion {
  projectId: string;
  versionId: string;
  schemaVersion: 1;
  baseGeneration: number;
  status: 'draft' | 'complete';
  createdAt: string;
  spec: AnalysisSpec;
  registryMetadata: RegistryMetadata;
  pointStyles: PointStyleState;
  activeTabId: string;
  manifests: Record<string, ResultManifest>;
  chunkRefs: Record<string, string[]>;
  provenance: Record<string, SourceRef | null>;
  edits: LocalEdit[];
  exclusions: ExcludedRow[];
  notes: string;
  extras?: Record<string, JsonValue>;
}
export interface ProjectVersionWithRows {
  head: ProjectHead;
  version: ProjectVersion;
  datasets: Record<string, AnalysisDataset>;
}
export interface ProjectBackup {
  format: 'aln-analysis-project';
  schema_version: 1;
  exported_at: string;
  project: ProjectVersionWithRows;
}
export const PROJECT_IMPORT_MAX_BYTES = 100 * 1024 * 1024;
export const PROJECT_IMPORT_MAX_ROWS = 500000;
export const PROJECT_CHUNK_ROWS = 2000;
export type ProjectErrorCode = 'UNAVAILABLE' | 'QUOTA' | 'CONFLICT' | 'CORRUPT' | 'UNSUPPORTED_VERSION';
export class ProjectError extends Error {
  constructor(public code: ProjectErrorCode, message: string) { super(message); }
}
