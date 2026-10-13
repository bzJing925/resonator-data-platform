import React, { useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { AnalysisWorkspaceProvider, useAnalysisWorkspace } from '../../src/features/analysis/workspace';
import ReportDialog from '../../src/features/analysis/report/ReportDialog';
import ProjectSaveBadge from '../../src/features/analysis/project/ProjectSaveBadge';
import { serializeWorksheetCsv } from '../../src/features/analysis/csv';
import { freezeReportInput } from '../../src/features/analysis/report/reportModel';
function Harness() {
  const api = useAnalysisWorkspace();
  useEffect(() => { Object.assign(window, { customerReport: {
    load(rows: Record<string, unknown>[]) {
      api.loadQueryRows(rows);
      api.updateNotes('客户结果表快照；原始ZIP已删除，算法及处理频段未知，不包含原始曲线。');
      api.updateSpec((spec) => ({ ...spec, units: { ...spec.units, qs: '1', fs_ghz: 'GHz' },
        chart: { ...spec.chart, type: 'box', x_fields: ['s_param_port'],
          y_fields: [{ name: 'qs', aggregation: 'all' }] } }));
    },
    rows: api.datasets[0]?.rows ?? [],
    csv: () => serializeWorksheetCsv(api.datasets[0]?.rows ?? []),
    async frozen() {
      const head = await api.project.saveNow();
      if (!head) throw new Error('Missing saved project');
      return freezeReportInput(await api.project.store.load(head.projectId)).summaries;
    },
  } }); }, [api]);
  return <main><ProjectSaveBadge project={api.project} /><button onClick={() => { api.loadQueryRows([{ eg: 1, qs: 10 }, { eg: 2, qs: 20 }, { eg: 3, qs: null }]); api.updateNotes('<script>window.evil=true;fetch("https://bad.test")</script><img src=x onerror=alert(1)>'); }}>加载报告夹具</button><ReportDialog /></main>;
}
createRoot(document.getElementById('root')!).render(<AnalysisWorkspaceProvider><Harness /></AnalysisWorkspaceProvider>);
