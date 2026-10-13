import { useEffect, useState } from 'react';
import { useAnalysisWorkspace } from '../features/analysis/workspace';
import type { ProjectHead } from '../features/analysis/project/projectTypes';
export function recentProjects(heads: ProjectHead[]) { return [...heads].sort((a, b) => Date.parse(b.lastOpenedAt) - Date.parse(a.lastOpenedAt)).slice(0, 5); }
export default function useRecentProjects(refresh = 0) {
  const { project } = useAnalysisWorkspace();
  const [heads, setHeads] = useState<ProjectHead[] | null>(null), [error, setError] = useState('');
  useEffect(() => { let alive = true; setError(''); void project.store.list().then((values) => { if (alive) setHeads(recentProjects(values)); }).catch((failure) => { if (alive) { setHeads(null); setError(String(failure)); } }); return () => { alive = false; }; }, [project.store, project.head?.currentVersionId, refresh]);
  return { heads, error };
}
