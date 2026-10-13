import { IS_STATIC } from '../../src/config';
import React, { useEffect, useState } from 'react';
import { UnifiedChartGrid } from '../../src/components/Charts';
import { createRoot } from 'react-dom/client';
import { AnalysisWorkspaceProvider, useAnalysisWorkspace } from '../../src/features/analysis/workspace';
import ProjectMenu from '../../src/features/analysis/project/ProjectMenu';
import ProjectSaveBadge from '../../src/features/analysis/project/ProjectSaveBadge';
import { platformRows, sourceA } from '../../src/features/analysis/__fixtures__/platformFixtures';

function Harness() {
  const [showChart, setShowChart] = useState(false);
  const api = useAnalysisWorkspace(), dataset = api.datasets[0];
  useEffect(() => { Object.assign(window, { workspaceHarness: api, workspaceStatic: IS_STATIC }); }, [api]);
  return <main>
    <ProjectSaveBadge project={api.project} />
    <ProjectMenu />
    <button data-testid="load-rows" onClick={() => api.loadQueryRows(platformRows(2001).map((row) => ({ ...row, _source_ref: sourceA })))}>加载行</button>
    <button data-testid="edit-row" disabled={!dataset} onClick={() => api.editCell(dataset.id, dataset.rows.at(-1)!.rowId, 'qs', 991)}>编辑末行</button>
    <button data-testid="exclude-row" disabled={!dataset} onClick={() => api.deleteRows(dataset.id, [dataset.rows.at(-1)!.rowId])}>排除末行</button>
    <button data-testid="select-row" disabled={!dataset} onClick={() => api.selectRows(dataset.id, [dataset.rows[0].rowId])}>选中</button>
    <button data-testid="show-chart" onClick={() => setShowChart(true)}>显示图形</button>
    {showChart && dataset && <UnifiedChartGrid chartType="scatter" rows={dataset.rows}
      xFields={['eg']} yFields={[{ name: 'qs', aggregation: 'all' }]} zField={null}
      datasetRevision={dataset.revision} selectedRowIds={api.selection.rowIds} analysisLayout />}
    <button data-testid="undo" disabled={!api.canUndo} onClick={api.undo}>撤销</button>
    <button data-testid="change-chart" onClick={() => api.updateSpec((s) => ({ ...s, chart: { ...s.chart, type: 'box' } }))}>改变图形</button>
    <output data-testid="row-count">{dataset?.rows.length ?? 0}</output>
    <output data-testid="last-value">{dataset?.rows.at(-1)?.qs ?? ''}</output>
    <output data-testid="chart-type">{api.state.spec.chart.type}</output>
  </main>;
}
createRoot(document.getElementById('root')!).render(<AnalysisWorkspaceProvider><Harness /></AnalysisWorkspaceProvider>);
