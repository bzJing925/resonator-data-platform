import React, { useEffect, useState } from 'react';
import I from '../../components/Icons';
import type { AnalysisDataset } from './workspace';

interface WorkspaceTabsProps {
  datasets: AnalysisDataset[];
  activeTabId: string;
  chartDatasetId: string | null;
  onActivate: (tabId: string) => void;
  onAdd: () => void;
  onDuplicate: (datasetId: string) => void;
  onRename: (datasetId: string, name: string) => void;
  onClose: (datasetId: string, replacementDatasetId?: string | null) => void;
}

export default function WorkspaceTabs({
  datasets,
  activeTabId,
  chartDatasetId,
  onActivate,
  onAdd,
  onDuplicate,
  onRename,
  onClose,
}: WorkspaceTabsProps) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftName, setDraftName] = useState('');
  const [menu, setMenu] = useState<{ datasetId: string; x: number; y: number } | null>(null);
  const [closeRequestId, setCloseRequestId] = useState<string | null>(null);
  const [replacementId, setReplacementId] = useState('');

  useEffect(() => {
    if (editingId && !datasets.some((dataset) => dataset.id === editingId)) setEditingId(null);
    if (menu && !datasets.some((dataset) => dataset.id === menu.datasetId)) setMenu(null);
    if (closeRequestId && !datasets.some((dataset) => dataset.id === closeRequestId)) {
      setCloseRequestId(null);
    }
  }, [closeRequestId, datasets, editingId, menu]);

  const beginRename = (dataset: AnalysisDataset) => {
    setEditingId(dataset.id);
    setDraftName(dataset.name);
    setMenu(null);
  };

  const commitRename = () => {
    if (editingId) onRename(editingId, draftName);
    setEditingId(null);
  };

  const requestClose = (datasetId: string) => {
    setMenu(null);
    if (datasetId !== chartDatasetId) {
      onClose(datasetId);
      return;
    }
    const replacement = datasets.find((dataset) => dataset.id !== datasetId)?.id || '';
    setReplacementId(replacement);
    setCloseRequestId(datasetId);
  };

  const requestedDataset = datasets.find((dataset) => dataset.id === closeRequestId);

  return (
    <div className="analysis-tabs-wrap">
      {requestedDataset && (
        <div className="analysis-tab-reference" role="dialog" aria-label="关闭图表数据源">
          <span>“{requestedDataset.name}”正在被图表引用</span>
          <select
            aria-label="切换图表数据源"
            value={replacementId}
            onChange={(event) => setReplacementId(event.target.value)}
          >
            <option value="">解除图表数据源引用</option>
            {datasets.filter((dataset) => dataset.id !== requestedDataset.id).map((dataset) => (
              <option value={dataset.id} key={dataset.id}>切换到 {dataset.name}</option>
            ))}
          </select>
          <button type="button" onClick={() => setCloseRequestId(null)}>取消</button>
          <button
            type="button"
            onClick={() => {
              onClose(requestedDataset.id, replacementId || null);
              setCloseRequestId(null);
            }}
          >
            关闭并继续
          </button>
        </div>
      )}
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
          <div className="analysis-tab-item" key={dataset.id}>
            {editingId === dataset.id ? (
              <input
                aria-label={`重命名 ${dataset.name}`}
                autoFocus
                value={draftName}
                onChange={(event) => setDraftName(event.target.value)}
                onBlur={commitRename}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') event.currentTarget.blur();
                  if (event.key === 'Escape') setEditingId(null);
                }}
              />
            ) : (
              <button
                role="tab"
                aria-selected={activeTabId === dataset.id}
                className={activeTabId === dataset.id ? 'active' : ''}
                onClick={() => onActivate(dataset.id)}
                onDoubleClick={() => beginRename(dataset)}
                onContextMenu={(event) => {
                  event.preventDefault();
                  setMenu({
                    datasetId: dataset.id,
                    x: Math.min(event.clientX, window.innerWidth - 150),
                    y: Math.min(event.clientY, window.innerHeight - 110),
                  });
                }}
                title={dataset.id === chartDatasetId ? '当前图表数据源' : undefined}
              >
                <I.table size={13} /> {dataset.name}{dataset.id === chartDatasetId ? ' ●' : ''}
              </button>
            )}
            {activeTabId === dataset.id && editingId !== dataset.id && (
              <span className="analysis-tab-actions">
                <button
                  type="button"
                  aria-label={`重命名 ${dataset.name}`}
                  title="重命名数据表"
                  onClick={() => beginRename(dataset)}
                >
                  <I.edit size={12} />
                </button>
                <button
                  type="button"
                  aria-label={`关闭 ${dataset.name}`}
                  title="关闭数据表"
                  onClick={() => requestClose(dataset.id)}
                >
                  <I.x size={12} />
                </button>
              </span>
            )}
          </div>
        ))}
        <button
          className="analysis-tab-add"
          onClick={onAdd}
          title="新建空白数据表"
          aria-label="新建空白数据表"
        >
          <I.plus size={13} />
        </button>
      </div>
      {menu && (() => {
        const dataset = datasets.find((item) => item.id === menu.datasetId);
        if (!dataset) return null;
        return (
          <div
            className="analysis-tab-menu"
            role="menu"
            style={{ left: menu.x, top: menu.y }}
          >
            <button role="menuitem" onClick={() => beginRename(dataset)}>重命名</button>
            <button
              role="menuitem"
              onClick={() => {
                onDuplicate(dataset.id);
                setMenu(null);
              }}
            >
              复制数据表
            </button>
            <button role="menuitem" onClick={() => requestClose(dataset.id)}>关闭数据表</button>
          </div>
        );
      })()}
    </div>
  );
}
