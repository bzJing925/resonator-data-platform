export type AnalysisDesktopCommand =
  | 'deleteSelection'
  | 'undo'
  | 'redo'
  | 'selectAll'
  | 'copySelection'
  | 'exportSelection'
  | 'editStyle'
  | 'autorange';
export type AnalysisSurface = 'chart' | 'worksheet';

export interface AnalysisCommandMessage {
  command: AnalysisDesktopCommand;
  surface: AnalysisSurface;
}
