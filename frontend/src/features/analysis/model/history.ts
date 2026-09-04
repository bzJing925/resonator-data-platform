export interface HistoryEntry<T> {
  label: string;
  before: T;
  after: T;
}

export interface HistoryState<T> {
  undoStack: HistoryEntry<T>[];
  redoStack: HistoryEntry<T>[];
}

export function createHistoryState<T>(): HistoryState<T> {
  return { undoStack: [], redoStack: [] };
}

export function pushHistory<T>(
  history: HistoryState<T>,
  entry: HistoryEntry<T>,
): HistoryState<T> {
  return { undoStack: [...history.undoStack, entry], redoStack: [] };
}

export function undoHistory<T>(
  present: T,
  history: HistoryState<T>,
): { present: T; history: HistoryState<T> } {
  const entry = history.undoStack.at(-1);
  if (!entry) return { present, history };
  return {
    present: entry.before,
    history: {
      undoStack: history.undoStack.slice(0, -1),
      redoStack: [...history.redoStack, entry],
    },
  };
}

export function redoHistory<T>(
  present: T,
  history: HistoryState<T>,
): { present: T; history: HistoryState<T> } {
  const entry = history.redoStack.at(-1);
  if (!entry) return { present, history };
  return {
    present: entry.after,
    history: {
      undoStack: [...history.undoStack, entry],
      redoStack: history.redoStack.slice(0, -1),
    },
  };
}
