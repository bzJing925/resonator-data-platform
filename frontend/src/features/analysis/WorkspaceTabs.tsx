import React from 'react';
import I from '../../components/Icons';
import type { AnalysisDataset } from './workspace';

interface WorkspaceTabsProps {
  datasets: AnalysisDataset[];
  activeTabId: string;
  chartDatasetId: string | null;
  onActivate: (tabId: string) => void;
  onDuplicate: () => void;
}

export default function WorkspaceTabs({
  datasets, activeTabId, chartDatasetId, onActivate, onDuplicate,
}: WorkspaceTabsProps) {
  return (
    <div className="analysis-tabs" role="tablist" aria-label="分析工作区页面">
      <button
        role="tab"
        aria-selected={activeTabId === 'chart'}
        className={activeTabId === 'chart' ? 'active' : ''}
        onClick={() => onActivate('chart')}
      >
        <I.scatter size={13} /> 图表
      </button>
      {datasets.map((dataset) => (
        <button
          key={dataset.id}
          role="tab"
          aria-selected={activeTabId === dataset.id}
          className={activeTabId === dataset.id ? 'active' : ''}
          onClick={() => onActivate(dataset.id)}
          title={dataset.id === chartDatasetId ? '当前图表数据源' : undefined}
        >
          <I.table size={13} /> {dataset.name}{dataset.id === chartDatasetId ? ' ●' : ''}
        </button>
      ))}
      <button
        className="analysis-tab-add"
        onClick={onDuplicate}
        disabled={!chartDatasetId}
        title="复制当前图表数据为派生表"
      >
        <I.plus size={13} />
      </button>
    </div>
  );
}
