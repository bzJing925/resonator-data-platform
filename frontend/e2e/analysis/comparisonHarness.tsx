import React, { useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { AnalysisWorkspaceProvider, useAnalysisWorkspace } from '../../src/features/analysis/workspace';
import WorksheetGrid from '../../src/features/analysis/WorksheetGrid';
import ComparisonView from '../../src/features/analysis/comparison/ComparisonView';
import { makeComparison } from '../../src/features/analysis/comparison/comparisonSpec';
import { selectedRowsForDataset } from '../../src/features/analysis/model/selection';
function Harness() {
  const api = useAnalysisWorkspace();
  useEffect(() => { Object.assign(window, { comparisonHarness: api }); }, [api]);
  return <main><button onClick={() => {
    const ids = [[10,20,null,40],[20,30,40,null]].map((values) => api.loadQueryRows(values.map((qs, i) => ({ qs, s_param_port: 'S11', structureType: 'P2', wafer: i === 3 ? 'W2' : 'W1', x: i % 2, y: 0 }))));
    const endpoint = (dataset_id: string) => ({ dataset_id, source: null, query_spec: api.state.spec.query_spec });
    api.updateSpec((spec) => ({ ...spec, comparison: makeComparison(endpoint(ids[0]), endpoint(ids[1]), 'workspace_snapshot', ['qs']) }));
  }}>加载比较夹具</button><ComparisonView fields={[{ name: 'qs', unit: '1' }]} />
  {api.datasets.map((dataset, i) => <div key={dataset.id} data-testid={`worksheet-${i}`} style={{ height: 250 }}><WorksheetGrid dataset={dataset} selectedRowIds={selectedRowsForDataset(api.state.selection, dataset.id)} onSelection={(ids, mode) => api.selectRows(dataset.id, ids, mode, 'worksheet')} onEdit={(id, column, value) => api.editCell(dataset.id, id, column, value)} /></div>)}
  </main>;
}
createRoot(document.getElementById('root')!).render(<AnalysisWorkspaceProvider><Harness /></AnalysisWorkspaceProvider>);
