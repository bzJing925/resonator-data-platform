import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { AnalysisWorkspaceProvider, useAnalysisWorkspace } from '../../src/features/analysis/workspace';
import { queryDevices, inspectComparisonConditions } from '../../src/api/endpoints';
import { getCapabilities } from '../../src/api/capabilities';
import { loadComparisonCohorts } from '../../src/features/analysis/comparison/comparisonData';
import ProjectSaveBadge from '../../src/features/analysis/project/ProjectSaveBadge';
import ReportDialog from '../../src/features/analysis/report/ReportDialog';
import ExportDialog from '../../src/export/ExportDialog';
import api from '../../src/api/client';
import { IS_STATIC } from '../../src/config';
import type { AnalysisComparison } from '../../src/types/platformContracts';
function Harness() {
  const workspace = useAnalysisWorkspace(), [exporting, setExporting] = useState(false);
  useEffect(() => { Object.assign(window, { platformFlow: {
    workspace, isStatic: IS_STATIC, getCapabilities,
    async query() {
      const result = await queryDevices({ filters: {}, fields: ['qs', 'eg', 'batch_no'], limit: 2 });
      workspace.loadQueryRows(result.rows, null, result.manifest);
      workspace.updateSpec((spec) => ({ ...spec, units: { ...spec.units, qs: '1' }, chart: { ...spec.chart, type: 'scatter', x_fields: ['eg'], y_fields: [{ name: 'qs', aggregation: 'all' }] } }));
      return result;
    },
    async compare() {
      const query = await queryDevices({ filters: {}, fields: ['qs', 'batch_no'], limit: 200000 });
      const refs = query.manifest.source_refs;
      const refFor = (name: string) => { const row = query.rows.find((row) => row.batch_no === name); const identity = row?._source_ref as { source_batch_id: string } | undefined; return refs.find((r) => r.source_batch_id === identity?.source_batch_id)!; };
      const a = IS_STATIC ? refs[0] : refFor('a');
      const b = IS_STATIC ? refs[1] : refFor('d');
      const comparison: AnalysisComparison = { scope: 'source', a: { source: a, dataset_id: null, query_spec: { filters: {}, fields: [], order_by: null } }, b: { source: b, dataset_id: null, query_spec: { filters: {}, fields: [], order_by: null } }, metrics: ['qs'], group_fields: [], common_frequency_ghz: null, condition_policy: 'require_equal', wafer_metric: null, unified_color_scale: true };
      const conditions = await inspectComparisonConditions({ comparison, quality_rule: null });
      const result = await loadComparisonCohorts(comparison, null, [], { qs: '1' });
      return { comparison, conditions, result };
    },
    async status() {
      const q = await queryDevices({ filters: {}, fields: ['batch_no'], limit: 1 });
      return (await api.get(`/batches/${encodeURIComponent(String(q.rows[0].batch_no))}/sync-status`)).data;
    },
  } }); }, [workspace]);
  const dataset = workspace.datasets[0];
  return <main><ProjectSaveBadge project={workspace.project} />
    <output data-testid="rows">{dataset?.rows.length ?? 0}</output><output data-testid="first-value">{String(dataset?.rows[0]?.qs ?? '')}</output>
    <ReportDialog /><button onClick={() => setExporting(true)}>完整导出</button>
    {exporting && dataset && <ExportDialog dataset={dataset} onClose={() => setExporting(false)} />}
  </main>;
}
createRoot(document.getElementById('root')!).render(<AnalysisWorkspaceProvider><Harness /></AnalysisWorkspaceProvider>);
