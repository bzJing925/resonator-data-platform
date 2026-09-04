import {
  addDatasetToRegistry,
  buildWorksheetColumns,
  CHART_PAGE_ID,
  normalizeAreaReality,
  normalizeStructureType,
  removeDatasetFromRegistry,
  updateDatasetInRegistry,
  type AnalysisDataset,
  type DatasetId,
  type DatasetRegistry,
  type RowId,
} from './dataset';
import {
  createSelectionState,
  removeRowsFromSelection,
  selectRows,
  type ActiveSurface,
  type SelectionMode,
  type SelectionState,
} from './selection';

export interface PointStyleOverride {
  color?: string;
  size?: number;
  symbol?: string;
  opacity?: number;
  outlineColor?: string;
  outlineWidth?: number;
}

export type PointStylePatch = {
  [Key in keyof PointStyleOverride]?: PointStyleOverride[Key] | null;
};

export type PointStyleState = Record<DatasetId, Record<string, PointStyleOverride>>;

const METRIC_STYLE_PREFIX = '@@analysis-metric/';

export function pointStyleMetricKey(metric: string) {
  return `${METRIC_STYLE_PREFIX}${encodeURIComponent(metric)}`;
}

export interface WorkspacePresent {
  registry: DatasetRegistry;
  activeTabId: string;
  selection: SelectionState;
  pointStyles: PointStyleState;
}

export type AnalysisCommand =
  | {
      type: 'selectRows';
      datasetId: DatasetId;
      rowIds: RowId[];
      mode?: SelectionMode;
      anchorRowId?: RowId | null;
      activeSurface?: ActiveSurface;
    }
  | { type: 'activateTab'; tabId: string; activeSurface?: ActiveSurface }
  | { type: 'setChartDataset'; datasetId: DatasetId }
  | { type: 'deleteRows'; datasetId: DatasetId; rowIds: RowId[] }
  | {
      type: 'updateRows';
      datasetId: DatasetId;
      updates: Array<{ rowId: RowId; changes: Record<string, unknown> }>;
    }
  | { type: 'addDataset'; dataset: AnalysisDataset; activate?: 'chart' | 'dataset' | false }
  | { type: 'duplicateDataset'; dataset: AnalysisDataset }
  | { type: 'renameDataset'; datasetId: DatasetId; name: string }
  | {
      type: 'closeDataset';
      datasetId: DatasetId;
      replacementDatasetId?: DatasetId | null;
    }
  | {
      type: 'updatePointStyle';
      datasetId: DatasetId;
      rowIds: RowId[];
      patch: PointStylePatch;
      metric?: string;
    }
  | { type: 'batch'; label: string; commands: AnalysisCommand[] };

const IDENTITY_FIELDS = new Set(['rowId', '__rowId', 'datasetId']);

function registryWithChartDataset(registry: DatasetRegistry, datasetId: DatasetId | null) {
  const chartPage = registry.chartPages[CHART_PAGE_ID];
  if (!chartPage || chartPage.datasetId === datasetId) return registry;
  return {
    ...registry,
    chartPages: {
      ...registry.chartPages,
      [CHART_PAGE_ID]: { ...chartPage, datasetId },
    },
  };
}

function executeSelectRows(present: WorkspacePresent, command: Extract<AnalysisCommand, {
  type: 'selectRows';
}>) {
  const dataset = present.registry.byId[command.datasetId];
  if (!dataset) return present;
  const validIds = new Set(dataset.rows.map((row) => row.rowId));
  const rowIds = command.rowIds.filter((rowId) => validIds.has(rowId));
  return {
    ...present,
    selection: selectRows(present.selection, { ...command, rowIds }),
  };
}

function executeUpdateRows(present: WorkspacePresent, command: Extract<AnalysisCommand, {
  type: 'updateRows';
}>) {
  const dataset = present.registry.byId[command.datasetId];
  if (!dataset || command.updates.length === 0) return present;
  const updates = new Map(command.updates.map((update) => [update.rowId, update.changes]));
  const knownColumns = new Set(dataset.columns.map((column) => column.key));
  const addsColumn = command.updates.some((update) => Object.keys(update.changes)
    .some((key) => !IDENTITY_FIELDS.has(key) && !knownColumns.has(key)));
  let changed = false;
  const rows = dataset.rows.map((row) => {
    const requested = updates.get(row.rowId);
    if (!requested) return row;
    const changes: Record<string, unknown> = Object.fromEntries(
      Object.entries(requested).filter(([key, value]) =>
        !IDENTITY_FIELDS.has(key) && row[key] !== value),
    );
    if ('structureType' in changes) {
      changes.structureType = normalizeStructureType(changes.structureType);
    } else if (dataset.fieldMap.structureType
      && dataset.fieldMap.structureType in changes) {
      changes.structureType = normalizeStructureType(changes[dataset.fieldMap.structureType]);
    }
    if ('areaReality' in changes) {
      changes.areaReality = normalizeAreaReality(changes.areaReality);
    } else if (dataset.fieldMap.areaReality && dataset.fieldMap.areaReality in changes) {
      changes.areaReality = normalizeAreaReality(changes[dataset.fieldMap.areaReality]);
    }
    Object.entries(changes).forEach(([key, value]) => {
      if (row[key] === value) delete changes[key];
    });
    if (Object.keys(changes).length === 0) return row;
    changed = true;
    return { ...row, ...changes };
  });
  if (!changed) return present;
  const nextDataset = {
    ...dataset,
    rows,
    columns: addsColumn ? buildWorksheetColumns(rows, dataset.fieldMap) : dataset.columns,
    revision: dataset.revision + 1,
  };
  return {
    ...present,
    registry: updateDatasetInRegistry(present.registry, nextDataset),
  };
}

function executeDeleteRows(present: WorkspacePresent, command: Extract<AnalysisCommand, {
  type: 'deleteRows';
}>) {
  const dataset = present.registry.byId[command.datasetId];
  if (!dataset || command.rowIds.length === 0) return present;
  const removed = new Set(command.rowIds);
  const rows = dataset.rows.filter((row) => !removed.has(row.rowId));
  if (rows.length === dataset.rows.length) return present;
  const nextDataset = { ...dataset, rows, revision: dataset.revision + 1 };
  const datasetStyles = present.pointStyles[dataset.id];
  let pointStyles = present.pointStyles;
  if (datasetStyles && [...removed].some((rowId) => datasetStyles[rowId])) {
    const keptStyles = Object.fromEntries(
      Object.entries(datasetStyles).filter(([rowId]) => !removed.has(rowId)),
    );
    pointStyles = { ...present.pointStyles, [dataset.id]: keptStyles };
  }
  return {
    ...present,
    registry: updateDatasetInRegistry(present.registry, nextDataset),
    selection: present.selection.datasetId === dataset.id
      ? removeRowsFromSelection(present.selection, removed)
      : present.selection,
    pointStyles,
  };
}

function executeAddDataset(present: WorkspacePresent, command: Extract<AnalysisCommand, {
  type: 'addDataset' | 'duplicateDataset';
}>): WorkspacePresent {
  const registry = addDatasetToRegistry(present.registry, command.dataset);
  if (registry === present.registry) return present;
  const activate = command.type === 'duplicateDataset' ? 'dataset' : command.activate;
  const withSource = command.type === 'addDataset' && activate === 'chart'
    ? registryWithChartDataset(registry, command.dataset.id)
    : registry;
  return {
    ...present,
    registry: withSource,
    activeTabId: activate === 'chart' ? CHART_PAGE_ID
      : activate === 'dataset' ? command.dataset.id : present.activeTabId,
    selection: activate
      ? {
          ...createSelectionState(),
          datasetId: command.dataset.id,
          activeSurface: activate === 'chart' ? 'chart' : 'worksheet',
        }
      : present.selection,
  };
}

function executeCloseDataset(present: WorkspacePresent, command: Extract<AnalysisCommand, {
  type: 'closeDataset';
}>): WorkspacePresent {
  if (!present.registry.byId[command.datasetId]) return present;
  const chartReferencesDataset = Object.values(present.registry.chartPages)
    .some((page) => page.datasetId === command.datasetId);
  if (chartReferencesDataset && command.replacementDatasetId === undefined) return present;
  if (command.replacementDatasetId === command.datasetId) return present;
  if (command.replacementDatasetId !== null
    && command.replacementDatasetId !== undefined
    && !present.registry.byId[command.replacementDatasetId]) return present;

  let registry = removeDatasetFromRegistry(present.registry, command.datasetId);
  if (chartReferencesDataset) {
    registry = registryWithChartDataset(registry, command.replacementDatasetId || null);
  }
  const { [command.datasetId]: _removedStyles, ...pointStyles } = present.pointStyles;
  return {
    ...present,
    registry,
    activeTabId: present.activeTabId === command.datasetId ? CHART_PAGE_ID : present.activeTabId,
    selection: present.selection.datasetId === command.datasetId
      ? { ...createSelectionState(), activeSurface: 'chart' }
      : present.selection,
    pointStyles,
  };
}

function executeUpdatePointStyle(present: WorkspacePresent, command: Extract<AnalysisCommand, {
  type: 'updatePointStyle';
}>) {
  const dataset = present.registry.byId[command.datasetId];
  const metric = command.metric?.trim();
  if (!dataset || (!metric && command.rowIds.length === 0)) return present;
  const validIds = new Set(dataset.rows.map((row) => row.rowId));
  const datasetStyles = { ...(present.pointStyles[command.datasetId] || {}) };
  let changed = false;
  const targetKeys = metric
    ? [pointStyleMetricKey(metric)]
    : command.rowIds.filter((rowId) => validIds.has(rowId));
  for (const targetKey of targetKeys) {
    const next = { ...(datasetStyles[targetKey] || {}) };
    for (const [key, value] of Object.entries(command.patch)) {
      if (value === null || value === undefined) delete next[key as keyof PointStyleOverride];
      else next[key as keyof PointStyleOverride] = value as never;
    }
    if (JSON.stringify(next) === JSON.stringify(datasetStyles[targetKey] || {})) continue;
    changed = true;
    if (Object.keys(next).length > 0) datasetStyles[targetKey] = next;
    else delete datasetStyles[targetKey];
  }
  if (!changed) return present;
  return {
    ...present,
    pointStyles: { ...present.pointStyles, [command.datasetId]: datasetStyles },
  };
}

export function executeAnalysisCommand(
  present: WorkspacePresent,
  command: AnalysisCommand,
): WorkspacePresent {
  switch (command.type) {
    case 'selectRows':
      return executeSelectRows(present, command);
    case 'activateTab': {
      const isDataset = !!present.registry.byId[command.tabId];
      const isChart = !!present.registry.chartPages[command.tabId];
      if (!isDataset && !isChart) return present;
      return {
        ...present,
        activeTabId: command.tabId,
        selection: command.activeSurface === undefined
          ? present.selection
          : { ...present.selection, activeSurface: command.activeSurface },
      };
    }
    case 'setChartDataset':
      if (!present.registry.byId[command.datasetId]) return present;
      return {
        ...present,
        registry: registryWithChartDataset(present.registry, command.datasetId),
        selection: {
          ...createSelectionState(), datasetId: command.datasetId, activeSurface: 'chart',
        },
      };
    case 'updateRows':
      return executeUpdateRows(present, command);
    case 'deleteRows':
      return executeDeleteRows(present, command);
    case 'addDataset':
    case 'duplicateDataset':
      return executeAddDataset(present, command);
    case 'renameDataset': {
      const dataset = present.registry.byId[command.datasetId];
      const name = command.name.trim();
      if (!dataset || !name || name === dataset.name) return present;
      return {
        ...present,
        registry: updateDatasetInRegistry(present.registry, { ...dataset, name }),
      };
    }
    case 'closeDataset':
      return executeCloseDataset(present, command);
    case 'updatePointStyle':
      return executeUpdatePointStyle(present, command);
    case 'batch':
      return command.commands.reduce(executeAnalysisCommand, present);
    default:
      return present;
  }
}

export function isUndoableCommand(command: AnalysisCommand): boolean {
  if (command.type === 'batch') return command.commands.some(isUndoableCommand);
  return !['selectRows', 'activateTab', 'setChartDataset'].includes(command.type);
}

export function commandLabel(command: AnalysisCommand) {
  if (command.type === 'batch') return command.label;
  return command.type;
}
