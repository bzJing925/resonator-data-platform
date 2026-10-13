import type { AnalysisSpec } from '../../../types/platformContracts';
import type { ProjectHead, ProjectVersionWithRows } from './projectTypes';

export interface ProjectTemplate { templateId: string; name: string; spec: AnalysisSpec }
export interface ProjectStore {
  list(): Promise<ProjectHead[]>;
  load(projectId: string): Promise<ProjectVersionWithRows>;
  save(input: ProjectVersionWithRows, expectedGeneration: number | null): Promise<ProjectHead>;
  remove(projectId: string, expectedGeneration: number): Promise<void>;
  saveTemplate(spec: AnalysisSpec, name: string): Promise<ProjectTemplate>;
  listTemplates(): Promise<ProjectTemplate[]>;
}
