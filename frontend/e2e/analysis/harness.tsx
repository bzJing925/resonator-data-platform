import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { UnifiedChartGrid } from '../../src/components/Charts';
import WorksheetGrid from '../../src/features/analysis/WorksheetGrid';
import WorkspaceTabs from '../../src/features/analysis/WorkspaceTabs';
import PointStyleInspector from '../../src/features/analysis/PointStyleInspector';
import {
  showAnalysisContextMenu,
  useAnalysisCommandBridge,
  type AnalysisDesktopCommand,
  type AnalysisSurface,
} from '../../src/features/analysis/commandBridge';
import { serializeWorksheetTsv } from '../../src/features/analysis/csv';
import { datumRowIds } from '../../src/features/analysis/chart/chartInteraction';
import {
  AnalysisWorkspaceProvider,
  useAnalysisWorkspace,
} from '../../src/features/analysis/workspace';
import {
  ANALYSIS_CHART_TYPES,
  ANALYSIS_ROWS,
  ANALYSIS_Y_FIELDS,
  AREA_REALITY_FIELD,
  STRUCTURE_TYPE_FIELD,
} from '../../src/features/analysis/__fixtures__/analysisRows';
import '../../src/styles.css';

function AnalysisHarness() {
  const renderCount = useRef(0);
  renderCount.current += 1;
  const [chartType, setChartType] = useState<(typeof ANALYSIS_CHART_TYPES)[number]>('scatter');
  const [chartMounted, setChartMounted] = useState(true);
  const [filtered, setFiltered] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [autorangeRequest, setAutorangeRequest] = useState(0);
  const [lastCommand, setLastCommand] = useState('');
  const {
    state,
    datasets,
    chartDataset: dataset,
    chartDatasetId,
    selection,
    pointStyles,
    loadQueryRows,
    addDataset,
    closeDataset,
    selectRows,
    setActiveTab,
    duplicateDataset,
    renameDataset,
    deleteSelection,
    updatePointStyle,
    undo,
    redo,
  } = useAnalysisWorkspace();

  useEffect(() => {
    if (datasets.length === 0) loadQueryRows(ANALYSIS_ROWS, { total: 24, returned: 24 });
  }, [datasets.length, loadQueryRows]);

  const runCommand = (command: AnalysisDesktopCommand, surface: AnalysisSurface) => {
    setLastCommand(`${command}:${surface}`);
    if (command === 'undo') undo();
    if (command === 'redo') redo();
    if (command === 'deleteSelection') deleteSelection();
    if (command === 'selectAll' && dataset) {
      selectRows(dataset.id, dataset.rows.map((row) => row.rowId), 'replace', surface);
    }
    if (command === 'copySelection' && dataset) {
      const selected = dataset.rows.filter((row) => selection.rowIds.has(row.rowId));
      if (selected.length > 0) {
        window.electronAPI?.writeAnalysisClipboard?.(serializeWorksheetTsv(selected));
      }
    }
    if (command === 'autorange' && surface === 'chart') {
      setAutorangeRequest((value) => value + 1);
    }
  };
  useAnalysisCommandBridge({
    surface: selection.activeSurface || (state.activeTabId === 'chart' ? 'chart' : 'worksheet'),
    onCommand: runCommand,
  });

  if (!dataset) return <main>加载固定数据…</main>;

  const onSelection = (event: any) => {
    const ids = new Set<string>();
    for (const point of event?.points || []) {
      datumRowIds(point.customdata).forEach((rowId) => ids.add(rowId));
    }
    const additive = !!(event?.event?.ctrlKey || event?.event?.metaKey || event?.event?.shiftKey);
    selectRows(dataset.id, [...ids], additive ? 'add' : 'replace', 'chart');
  };
  const onPointClick = (datum: unknown, event: any) => {
    const mode = event?.event?.metaKey || event?.event?.ctrlKey
      ? 'toggle'
      : event?.event?.shiftKey ? 'add' : 'replace';
    selectRows(dataset.id, datumRowIds(datum), mode, 'chart');
  };
  const onDeselect = () => selectRows(dataset.id, [], 'replace', 'chart');
  const chartRows = filtered ? dataset.rows.slice(0, 12) : dataset.rows;

  return (
    <div className={`app${fullscreen ? ' analysis-fullscreen' : ''}`}>
      <div className="titlebar" />
      <nav className="rail" data-testid="main-nav">导航</nav>
      <main className="main" style={{ padding: 16, overflow: 'auto', background: '#111318' }}>
      <label style={{ color: '#e7e9ed' }}>
        图形
        <select
          data-testid="chart-type"
          value={chartType}
          onChange={(event) => setChartType(event.target.value as typeof chartType)}
        >
          {ANALYSIS_CHART_TYPES.map((type) => <option key={type}>{type}</option>)}
        </select>
      </label>
      <output data-testid="selection">{[...selection.rowIds].join(',')}</output>
      <output data-testid="row-count">{dataset.rows.length}</output>
      <output data-testid="dataset-count">{datasets.length}</output>
      <output data-testid="active-tab">{state.activeTabId}</output>
      <output data-testid="last-command">{lastCommand}</output>
      <output data-testid="render-count">{renderCount.current}</output>
      <output data-testid="filter-state">{filtered ? 'filtered' : 'all'}</output>
      <output data-testid="fullscreen-state">{fullscreen ? 'fullscreen' : 'windowed'}</output>
      <input data-testid="editable-probe" defaultValue="editable text" />
      <button data-testid="toggle-filter" onClick={() => setFiltered((value) => !value)}>筛选</button>
      <button data-testid="toggle-chart" onClick={() => setChartMounted((value) => !value)}>切换 Tab</button>
      <button data-testid="toggle-fullscreen" onClick={() => setFullscreen((value) => !value)}>全屏</button>
      {chartMounted && <section
        data-testid="chart"
        data-fullscreen={fullscreen ? 'true' : 'false'}
        style={{ width: fullscreen ? '100vw' : 960 }}
        onContextMenu={(event) => showAnalysisContextMenu(event, 'chart')}
      >
        <UnifiedChartGrid
          chartType={chartType}
          rows={chartRows}
          xFields={[STRUCTURE_TYPE_FIELD]}
          yFields={ANALYSIS_Y_FIELDS}
          zField={AREA_REALITY_FIELD}
          selectedRowIds={selection.rowIds}
          pointStyles={pointStyles[dataset.id]}
          autorangeRequest={autorangeRequest}
          onSelection={onSelection}
          onPointClick={onPointClick}
          onDeselect={onDeselect}
          revision={`phase-0-${chartType}`}
          datasetRevision={dataset.revision}
          analysisLayout
        />
      </section>}
      <aside style={{ width: 340 }}>
        <PointStyleInspector
          dataset={dataset}
          selectedRowIds={selection.rowIds}
          anchorRowId={selection.anchorRowId}
          metrics={ANALYSIS_Y_FIELDS}
          styles={pointStyles[dataset.id]}
          onUpdate={(rowIds, patch, metric) =>
            updatePointStyle(dataset.id, rowIds, patch, metric)}
        />
      </aside>
      <section data-testid="worksheet" style={{ display: 'flex', width: 720, height: 360 }}>
        <WorksheetGrid
          dataset={dataset}
          selectedRowIds={selection.rowIds}
          onSelection={(ids, mode) => selectRows(dataset.id, ids, mode, 'worksheet')}
          onEdit={() => undefined}
          onDelete={() => runCommand('deleteSelection', 'worksheet')}
          onSelectAll={() => runCommand('selectAll', 'worksheet')}
          onContextMenu={(event) => showAnalysisContextMenu(event, 'worksheet')}
        />
      </section>
      <WorkspaceTabs
        datasets={datasets}
        activeTabId={state.activeTabId}
        chartDatasetId={chartDatasetId}
        onActivate={setActiveTab}
        onAdd={() => addDataset()}
        onDuplicate={duplicateDataset}
        onRename={renameDataset}
        onClose={closeDataset}
      />
      </main>
      <div className="statusbar" />
    </div>
  );
}

createRoot(document.getElementById('root')!).render(
  <AnalysisWorkspaceProvider>
    <AnalysisHarness />
  </AnalysisWorkspaceProvider>,
);
