import { expect, it } from 'vitest';
import { recentProjects } from './useRecentProjects';
import type { ProjectHead } from '../features/analysis/project/projectTypes';
it('recent projects are sorted by actual open time with a five-project bound', () => {
  const heads = Array.from({ length: 7 }, (_, i) => ({ projectId: String(i), lastOpenedAt: `2026-10-10T00:00:0${i}Z` } as ProjectHead));
  expect(recentProjects(heads).map((head) => head.projectId)).toEqual(['6', '5', '4', '3', '2']);
  expect(heads[0].projectId).toBe('0');
});
