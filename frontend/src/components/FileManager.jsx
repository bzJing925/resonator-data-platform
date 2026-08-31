import React, {
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import I from './Icons.jsx';
import {
  listFileTree,
  moveFileTreeNodes,
  reorderFileTreeNodes,
  mkdirFileTree,
  renameFileTreeNode,
  deleteFileTreeNodes,
  downloadFileTreeNodesZip,
} from '../api/endpoints.js';

function formatBytes(bytes) {
  if (bytes == null || bytes < 0) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function downloadBlob(blob, filename) {
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}

const FileIcon = memo(function FileIcon({ nodeType, name, computed }) {
  if (nodeType === 'folder' || nodeType === 'zip' || nodeType === 'root') {
    return <I.folder size={40} className="file-icon folder" />;
  }
  const ext = name.split('.').pop()?.toLowerCase();
  let icon = <I.file size={40} className="file-icon" />;
  if (ext === 's1p' || ext === 's1p.gz') icon = <I.file size={40} className="file-icon s1p" />;
  else if (ext === 's2p' || ext === 's2p.gz') icon = <I.file size={40} className="file-icon s2p" />;
  return (
    <div className="file-icon-wrap">
      {icon}
      {computed && <span className="computed-dot" title="已计算" />}
    </div>
  );
});

const FileItem = memo(function FileItem({
  node,
  selected,
  viewMode,
  onClick,
  onDoubleClick,
  onContextMenu,
  onMouseDown,
  onTouchStart,
  onTouchEnd,
  onDragStart,
  onDragOver,
  onDrop,
  dragOver,
}) {
  const isFolder = node.node_type !== 'file';
  const className = [
    'file-item',
    viewMode,
    selected ? 'selected' : '',
    dragOver ? 'drag-over' : '',
    isFolder ? 'folder' : 'file',
  ].join(' ');

  return (
    <div
      className={className}
      data-node-id={node.id}
      onClick={(e) => onClick(e, node)}
      onDoubleClick={(e) => onDoubleClick(e, node)}
      onContextMenu={(e) => onContextMenu(e, node)}
      onMouseDown={(e) => onMouseDown(e, node)}
      onTouchStart={(e) => onTouchStart(e, node)}
      onTouchEnd={(e) => onTouchEnd(e, node)}
      onDragStart={(e) => onDragStart(e, node)}
      onDragOver={(e) => onDragOver(e, node)}
      onDrop={(e) => onDrop(e, node)}
      draggable={node.node_type === 'file' || node.node_type === 'folder'}
    >
      <FileIcon nodeType={node.node_type} name={node.name} computed={node.computed} />
      <div className="file-info">
        <div className="file-name" title={node.name}>
          {node.name}
        </div>
        {viewMode === 'list' && (
          <div className="file-meta">
            <span>{node.node_type === 'file' ? formatBytes(node.size) : `${node.children_count} 项`}</span>
            {node.computed && <span className="badge computed">已计算</span>}
          </div>
        )}
      </div>
      {viewMode === 'grid' && node.computed && (
        <span className="badge computed grid-badge">已计算</span>
      )}
    </div>
  );
});

export default function FileManager({ batchNo, onError }) {
  const [nodes, setNodes] = useState([]);
  const [currentParentId, setCurrentParentId] = useState(null);
  const [breadcrumb, setBreadcrumb] = useState([]);
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [viewMode, setViewMode] = useState('grid');
  const [loading, setLoading] = useState(false);
  const [contextMenu, setContextMenu] = useState(null);
  const [renaming, setRenaming] = useState(null);
  const [newFolderParent, setNewFolderParent] = useState(null);
  const [dragOverNodeId, setDragOverNodeId] = useState(null);
  const [lastClickedId, setLastClickedId] = useState(null);

  const containerRef = useRef(null);
  const lassoRef = useRef(null);
  const longPressTimer = useRef(null);
  const lassoState = useRef(null);

  const loadNodes = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listFileTree(batchNo, currentParentId);
      setNodes(data || []);
    } catch (e) {
      onError?.(e.message || '加载文件列表失败');
    } finally {
      setLoading(false);
    }
  }, [batchNo, currentParentId, onError]);

  useEffect(() => {
    loadNodes();
  }, [loadNodes]);

  const selectedNodes = useMemo(
    () => nodes.filter((n) => selectedIds.has(n.id)),
    [nodes, selectedIds]
  );

  const navigateToNode = useCallback((node) => {
    if (!node || node.node_type === 'file') return;
    setCurrentParentId(node.id);
    setBreadcrumb((prev) => {
      const idx = prev.findIndex((n) => n.id === node.id);
      if (idx >= 0) return prev.slice(0, idx + 1);
      return [...prev, node];
    });
    setSelectedIds(new Set());
    setLastClickedId(null);
  }, []);

  const navigateUp = useCallback(
    (targetNode) => {
      if (!targetNode) {
        setCurrentParentId(null);
        setBreadcrumb([]);
      } else {
        navigateToNode(targetNode);
      }
      setSelectedIds(new Set());
      setLastClickedId(null);
    },
    [navigateToNode]
  );

  const handleSelect = useCallback(
    (e, node) => {
      e.stopPropagation();
      const isMulti = e.ctrlKey || e.metaKey;
      const isRange = e.shiftKey && lastClickedId != null;

      if (isRange) {
        const ids = nodes.map((n) => n.id);
        const start = ids.indexOf(lastClickedId);
        const end = ids.indexOf(node.id);
        if (start >= 0 && end >= 0) {
          const [a, b] = start < end ? [start, end] : [end, start];
          const rangeIds = ids.slice(a, b + 1);
          setSelectedIds((prev) => {
            const next = new Set(prev);
            rangeIds.forEach((id) => next.add(id));
            return next;
          });
        }
        return;
      }

      if (isMulti) {
        setSelectedIds((prev) => {
          const next = new Set(prev);
          if (next.has(node.id)) next.delete(node.id);
          else next.add(node.id);
          return next;
        });
      } else {
        setSelectedIds(new Set([node.id]));
      }
      setLastClickedId(node.id);
    },
    [lastClickedId, nodes]
  );

  const handleDoubleClick = useCallback(
    (e, node) => {
      e.stopPropagation();
      if (node.node_type !== 'file') {
        navigateToNode(node);
      }
    },
    [navigateToNode]
  );

  const handleContextMenu = useCallback((e, node) => {
    e.preventDefault();
    e.stopPropagation();
    setContextMenu({ x: e.clientX, y: e.clientY, node });
    if (node) {
      setSelectedIds((prev) => {
        if (prev.has(node.id)) return prev;
        return new Set([node.id]);
      });
    }
  }, []);

  const clearSelection = useCallback(() => {
    setSelectedIds(new Set());
    setLastClickedId(null);
    setContextMenu(null);
  }, []);

  // Lasso selection
  const onContainerMouseDown = useCallback(
    (e) => {
      if (e.button !== 0) return;
      // ignore if clicking on item or control
      if (e.target.closest('.file-item') || e.target.closest('.btn') || e.target.closest('input')) return;
      const rect = containerRef.current.getBoundingClientRect();
      lassoState.current = {
        startX: e.clientX,
        startY: e.clientY,
        left: e.clientX - rect.left,
        top: e.clientY - rect.top,
      };
      if (!e.ctrlKey && !e.metaKey) {
        setSelectedIds(new Set());
        setLastClickedId(null);
      }

      const onMove = (ev) => {
        const st = lassoState.current;
        if (!st) return;
        if (!st.active && (Math.abs(ev.clientX - st.startX) > 4 || Math.abs(ev.clientY - st.startY) > 4)) {
          st.active = true;
        }
        if (!st.active) return;
        const rect2 = containerRef.current.getBoundingClientRect();
        const x1 = Math.min(st.startX, ev.clientX) - rect2.left;
        const y1 = Math.min(st.startY, ev.clientY) - rect2.top;
        const x2 = Math.max(st.startX, ev.clientX) - rect2.left;
        const y2 = Math.max(st.startY, ev.clientY) - rect2.top;
        st.box = { left: x1, top: y1, width: x2 - x1, height: y2 - y1 };
        if (lassoRef.current) {
          lassoRef.current.style.left = `${x1}px`;
          lassoRef.current.style.top = `${y1}px`;
          lassoRef.current.style.width = `${x2 - x1}px`;
          lassoRef.current.style.height = `${y2 - y1}px`;
          lassoRef.current.style.display = 'block';
        }
        const newIds = new Set();
        containerRef.current.querySelectorAll('.file-item').forEach((el) => {
          const r = el.getBoundingClientRect();
          const cx = r.left + r.width / 2;
          const cy = r.top + r.height / 2;
          if (cx >= Math.min(st.startX, ev.clientX) && cx <= Math.max(st.startX, ev.clientX) &&
              cy >= Math.min(st.startY, ev.clientY) && cy <= Math.max(st.startY, ev.clientY)) {
            newIds.add(Number(el.dataset.nodeId));
          }
        });
        setSelectedIds((prev) => {
          if (e.ctrlKey || e.metaKey) {
            const merged = new Set(prev);
            newIds.forEach((id) => merged.add(id));
            return merged;
          }
          return newIds;
        });
      };

      const onUp = () => {
        if (lassoRef.current) lassoRef.current.style.display = 'none';
        lassoState.current = null;
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
      };

      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    },
    []
  );

  // Long press for touch / mouse
  const startLongPress = useCallback((node) => {
    longPressTimer.current = setTimeout(() => {
      setSelectedIds((prev) => {
        const next = new Set(prev);
        if (next.has(node.id)) next.delete(node.id);
        else next.add(node.id);
        return next;
      });
      setLastClickedId(node.id);
    }, 500);
  }, []);

  const cancelLongPress = useCallback(() => {
    if (longPressTimer.current) {
      clearTimeout(longPressTimer.current);
      longPressTimer.current = null;
    }
  }, []);

  const onItemMouseDown = useCallback(
    (e, node) => {
      if (e.button !== 0) return;
      startLongPress(node);
      const onUp = () => {
        cancelLongPress();
        window.removeEventListener('mouseup', onUp);
      };
      window.addEventListener('mouseup', onUp);
    },
    [startLongPress, cancelLongPress]
  );

  const onItemTouchStart = useCallback(
    (e, node) => {
      startLongPress(node);
    },
    [startLongPress]
  );
  const onItemTouchEnd = useCallback(() => {
    cancelLongPress();
  }, [cancelLongPress]);

  // Drag & drop: move to folder or reorder
  const onDragStart = useCallback((e, node) => {
    e.dataTransfer.setData('text/plain', String(node.id));
    e.dataTransfer.effectAllowed = 'move';
  }, []);

  const onDragOver = useCallback((e, node) => {
    if (node.node_type === 'file') return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    setDragOverNodeId(node.id);
  }, []);

  const onDragLeave = useCallback(() => {
    setDragOverNodeId(null);
  }, []);

  const onDrop = useCallback(
    async (e, targetNode) => {
      e.preventDefault();
      e.stopPropagation();
      setDragOverNodeId(null);
      const sourceId = Number(e.dataTransfer.getData('text/plain'));
      if (!sourceId || sourceId === targetNode.id) return;

      const sourceNode = nodes.find((n) => n.id === sourceId);
      if (!sourceNode) return;

      // Drop on folder => move
      if (targetNode.node_type !== 'file') {
        try {
          await moveFileTreeNodes({
            node_ids: [sourceId],
            target_folder_id: targetNode.id,
          });
          await loadNodes();
        } catch (err) {
          onError?.(err.message || '移动失败');
        }
        return;
      }

      // Drop on file in same folder => reorder
      if (sourceNode.parent_id === targetNode.parent_id) {
        const orderedIds = nodes.map((n) => n.id);
        const from = orderedIds.indexOf(sourceId);
        const to = orderedIds.indexOf(targetNode.id);
        if (from < 0 || to < 0) return;
        orderedIds.splice(from, 1);
        orderedIds.splice(to, 0, sourceId);
        try {
          await reorderFileTreeNodes({
            parent_id: sourceNode.parent_id,
            node_ids: orderedIds,
          });
          await loadNodes();
        } catch (err) {
          onError?.(err.message || '排序失败');
        }
      }
    },
    [nodes, loadNodes, onError]
  );

  // Actions
  const handleDownloadSelected = useCallback(async () => {
    if (selectedIds.size === 0) return;
    try {
      const res = await downloadFileTreeNodesZip(batchNo, Array.from(selectedIds));
      downloadBlob(res.data, `${batchNo}_selected.zip`);
    } catch (e) {
      onError?.(e.message || '下载失败');
    }
  }, [batchNo, selectedIds, onError]);

  const handleDeleteSelected = useCallback(async () => {
    if (selectedIds.size === 0) return;
    if (!window.confirm(`确定要删除选中的 ${selectedIds.size} 个项目吗？`)) return;
    try {
      await deleteFileTreeNodes({ node_ids: Array.from(selectedIds) });
      setSelectedIds(new Set());
      await loadNodes();
    } catch (e) {
      onError?.(e.message || '删除失败');
    }
  }, [selectedIds, loadNodes, onError]);

  const handleMkdir = useCallback(
    async (name) => {
      if (!name) return;
      try {
        await mkdirFileTree({
          batch_no: batchNo,
          parent_id: currentParentId,
          name,
        });
        setNewFolderParent(null);
        await loadNodes();
      } catch (e) {
        onError?.(e.message || '新建文件夹失败');
      }
    },
    [batchNo, currentParentId, loadNodes, onError]
  );

  const handleRename = useCallback(
    async (nodeId, name) => {
      if (!name) return;
      try {
        await renameFileTreeNode({ node_id: nodeId, name });
        setRenaming(null);
        await loadNodes();
      } catch (e) {
        onError?.(e.message || '重命名失败');
      }
    },
    [loadNodes, onError]
  );

  // Context menu actions
  const contextMenuActions = useMemo(() => {
    const items = [];
    if (selectedIds.size > 0) {
      items.push({ label: '下载', icon: 'download', onClick: handleDownloadSelected });
      items.push({ label: '删除', icon: 'trash', onClick: handleDeleteSelected, danger: true });
    }
    if (selectedIds.size <= 1 && contextMenu?.node?.node_type !== 'file') {
      items.push({ label: '新建文件夹', icon: 'folder', onClick: () => setNewFolderParent(contextMenu.node || { id: currentParentId }) });
    }
    if (contextMenu?.node && contextMenu.node.node_type !== 'root') {
      items.push({ label: '重命名', icon: 'edit', onClick: () => setRenaming(contextMenu.node) });
    }
    return items;
  }, [selectedIds, contextMenu, currentParentId, handleDownloadSelected, handleDeleteSelected]);

  useEffect(() => {
    const onClick = () => setContextMenu(null);
    window.addEventListener('click', onClick);
    return () => window.removeEventListener('click', onClick);
  }, []);

  return (
    <div className="file-manager" onContextMenu={(e) => { e.preventDefault(); handleContextMenu(e, null); }}>
      <div className="file-manager-toolbar">
        <div className="breadcrumb">
          <button className="btn ghost sm" onClick={() => navigateUp(null)}>{batchNo}</button>
          {breadcrumb.map((n, idx) => (
            <span key={n.id}>
              <span className="sep">›</span>
              <button className="btn ghost sm" onClick={() => navigateUp(n)}>{n.name}</button>
            </span>
          ))}
        </div>
        <div className="spacer" />
        <button className="btn sm" onClick={() => setNewFolderParent({ id: currentParentId })} title="新建文件夹">
          <I.folder size={13} /> 新建文件夹
        </button>
        <button
          className="btn sm"
          disabled={selectedIds.size === 0}
          onClick={handleDownloadSelected}
          title="下载选中"
        >
          <I.download size={13} /> 下载 ({selectedIds.size})
        </button>
        <button
          className="btn sm danger"
          disabled={selectedIds.size === 0}
          onClick={handleDeleteSelected}
          title="删除选中"
        >
          <I.trash size={13} /> 删除 ({selectedIds.size})
        </button>
        <div className="view-toggle">
          <button className={`btn sm ${viewMode === 'grid' ? 'active' : ''}`} onClick={() => setViewMode('grid')} title="网格">
            <I.grid size={13} />
          </button>
          <button className={`btn sm ${viewMode === 'list' ? 'active' : ''}`} onClick={() => setViewMode('list')} title="列表">
            <I.list size={13} />
          </button>
        </div>
      </div>

      <div
        ref={containerRef}
        className={`file-manager-body ${viewMode}`}
        onMouseDown={onContainerMouseDown}
        onClick={clearSelection}
        onDragLeave={onDragLeave}
      >
        {loading && nodes.length === 0 && (
          <div className="empty">加载中…</div>
        )}
        {!loading && nodes.length === 0 && (
          <div className="empty">暂无文件</div>
        )}
        {nodes.map((node) => (
          <FileItem
            key={node.id}
            node={node}
            selected={selectedIds.has(node.id)}
            viewMode={viewMode}
            onClick={handleSelect}
            onDoubleClick={handleDoubleClick}
            onContextMenu={handleContextMenu}
            onMouseDown={onItemMouseDown}
            onTouchStart={onItemTouchStart}
            onTouchEnd={onItemTouchEnd}
            onDragStart={onDragStart}
            onDragOver={onDragOver}
            onDrop={onDrop}
            dragOver={dragOverNodeId === node.id}
          />
        ))}
        <div ref={lassoRef} className="lasso" style={{ display: 'none' }} />
      </div>

      {newFolderParent && (
        <NamePrompt
          title="新建文件夹"
          onConfirm={handleMkdir}
          onCancel={() => setNewFolderParent(null)}
        />
      )}
      {renaming && (
        <NamePrompt
          title="重命名"
          defaultValue={renaming.name}
          onConfirm={(name) => handleRename(renaming.id, name)}
          onCancel={() => setRenaming(null)}
        />
      )}

      {contextMenu && contextMenuActions.length > 0 && (
        <div
          className="context-menu"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          onClick={(e) => e.stopPropagation()}
        >
          {contextMenuActions.map((item) => (
            <button
              key={item.label}
              className={`ctx-item ${item.danger ? 'danger' : ''}`}
              onClick={() => {
                item.onClick();
                setContextMenu(null);
              }}
            >
              <IconByName name={item.icon} size={13} />
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function NamePrompt({ title, defaultValue = '', onConfirm, onCancel }) {
  const [value, setValue] = useState(defaultValue);
  const ref = useRef(null);
  useLayoutEffect(() => {
    ref.current?.focus();
    ref.current?.select();
  }, []);

  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">{title}</div>
        <div className="modal-body">
          <input
            ref={ref}
            className="input"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') onConfirm(value);
              if (e.key === 'Escape') onCancel();
            }}
          />
        </div>
        <div className="modal-foot">
          <button className="btn" onClick={onCancel}>取消</button>
          <button className="btn primary" onClick={() => onConfirm(value)}>确定</button>
        </div>
      </div>
    </div>
  );
}

function IconByName({ name, size }) {
  const Icon = I[name];
  return Icon ? <Icon size={size} /> : null;
}
