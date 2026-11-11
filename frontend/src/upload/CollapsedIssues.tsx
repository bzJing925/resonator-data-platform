import React from 'react';

export interface CollapsedIssue {
  code: string;
  severity: string;
  message: string;
  target?: string;
}

export default function CollapsedIssues({ issues, resetKey }: { issues: CollapsedIssue[]; resetKey?: string | number }) {
  const groups = new Map<string, { label: string; targets: string[] }>();
  for (const issue of issues) {
    const key = `${issue.severity}:${issue.code}:${issue.message}`;
    const group = groups.get(key) ?? {
      label: issue.code === 'MAPPING_NOT_FOUND' ? '器件未命中对照表' : issue.message,
      targets: [],
    };
    if (issue.target && !group.targets.includes(issue.target)) group.targets.push(issue.target);
    groups.set(key, group);
  }
  if (!issues.length) return null;
  return <details key={resetKey} className="precheck-issues"><summary>检查问题（{issues.length} 项）</summary><div>
    {[...groups.entries()].map(([key, group]) => <p key={key}><strong>{group.label}</strong>{group.targets.length > 0 && <>：{group.targets.join('、')}</>}</p>)}
  </div></details>;
}
