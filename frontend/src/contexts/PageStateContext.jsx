import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

const PageStateContext = createContext(null);

const STORAGE_KEY = 'aln_page_state_v1';
const DEFAULT_MAX_DATA_BYTES = 1024 * 1024;
const SAVE_DELAY_MS = 500;

/**
 * JSON replacer that preserves Set and Map instances with explicit markers.
 */
function setMapReplacer(_key, value) {
  if (value instanceof Set) {
    return { __type: 'Set', values: Array.from(value) };
  }
  if (value instanceof Map) {
    return { __type: 'Map', entries: Array.from(value.entries()) };
  }
  return value;
}

/**
 * JSON reviver that restores Set and Map instances serialized by setMapReplacer.
 */
function setMapReviver(_key, value) {
  if (value && typeof value === 'object') {
    if (value.__type === 'Set' && Array.isArray(value.values)) {
      return new Set(value.values);
    }
    if (value.__type === 'Map' && Array.isArray(value.entries)) {
      return new Map(value.entries);
    }
  }
  return value;
}

function stringifyState(value) {
  return JSON.stringify(value, setMapReplacer);
}

function parseState(text) {
  try {
    return JSON.parse(text, setMapReviver);
  } catch {
    return null;
  }
}

function loadStateMap() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = parseState(raw);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

/**
 * Drop data-heavy fields when their total serialized size exceeds the limit.
 * Mutates the provided serializable object.
 */
function enforceDataLimit(serializable, dataKeys, maxDataBytes) {
  let dataSize = 0;
  const dropped = [];
  for (const key of dataKeys) {
    if (!(key in serializable)) continue;
    const size = JSON.stringify(serializable[key]).length;
    dataSize += size;
    if (dataSize > maxDataBytes) {
      dropped.push(key);
    }
  }
  for (const key of dropped) {
    delete serializable[key];
  }
}

function serializeStateMap(stateMap, optionsRef) {
  const persisted = {};
  for (const [key, state] of Object.entries(stateMap)) {
    const opts = optionsRef.current[key] || {};
    const { dataKeys = [], maxDataBytes = DEFAULT_MAX_DATA_BYTES } = opts;

    // Convert to plain JSON object first so Set/Map become serializable.
    const serializable = JSON.parse(stringifyState(state));
    enforceDataLimit(serializable, dataKeys, maxDataBytes);
    persisted[key] = serializable;
  }
  return JSON.stringify(persisted);
}

function saveStateMap(stateMap, optionsRef) {
  try {
    const text = serializeStateMap(stateMap, optionsRef);
    localStorage.setItem(STORAGE_KEY, text);
  } catch {
    // localStorage may be unavailable or quota exceeded; fail silently.
  }
}

/**
 * Merge persisted state over the initial state. Arrays and nested objects
 * are taken wholesale from persisted; callers that need deep merging should
 * normalize in their components.
 */
function mergePersisted(persisted, initial) {
  if (!persisted || typeof persisted !== 'object') return initial;
  return { ...initial, ...persisted };
}

export function PageStateProvider({ children }) {
  const [stateMap, setStateMap] = useState(loadStateMap);
  const optionsRef = useRef({});

  const setPageState = useCallback((key, updater) => {
    setStateMap((prev) => {
      const current = prev[key];
      const merged = current !== undefined ? mergePersisted(current, {}) : {};
      const next = typeof updater === 'function' ? updater(merged) : updater;
      return { ...prev, [key]: next };
    });
  }, []);

  const registerOptions = useCallback((key, options) => {
    optionsRef.current[key] = options;
  }, []);

  const unregisterOptions = useCallback((key) => {
    delete optionsRef.current[key];
  }, []);

  useEffect(() => {
    const id = setTimeout(() => saveStateMap(stateMap, optionsRef), SAVE_DELAY_MS);
    return () => clearTimeout(id);
  }, [stateMap]);

  const value = useMemo(
    () => ({ stateMap, setPageState, registerOptions, unregisterOptions }),
    [stateMap, setPageState, registerOptions, unregisterOptions],
  );

  return <PageStateContext.Provider value={value}>{children}</PageStateContext.Provider>;
}

/**
 * Hook for persisting page state across route switches and browser sessions.
 *
 * @param {string} key - Unique page key.
 * @param {object} initialState - Default state. Should be stable across renders
 *   (define outside the component or memoize with useMemo).
 * @param {object} [options] - Persistence options.
 * @param {string[]} [options.dataKeys=[]] - Field names considered data-heavy.
 *   These fields are dropped from localStorage if their total serialized size
 *   exceeds `maxDataBytes`. They remain in memory while the app is running.
 * @param {number} [options.maxDataBytes=1048576] - Max serialized bytes for
 *   dataKeys before dropping them from storage.
 *
 * @returns {[object, function, function]} - [state, setState, resetState]
 */
export function usePageState(key, initialState, options = {}) {
  const { dataKeys = [], maxDataBytes = DEFAULT_MAX_DATA_BYTES } = options;
  const ctx = useContext(PageStateContext);
  if (!ctx) {
    throw new Error('usePageState must be used within PageStateProvider');
  }
  const { stateMap, setPageState, registerOptions, unregisterOptions } = ctx;

  // Register persistence options for this key.
  useEffect(() => {
    registerOptions(key, { dataKeys, maxDataBytes });
    return () => unregisterOptions(key);
  }, [key, dataKeys, maxDataBytes, registerOptions, unregisterOptions]);

  const state = useMemo(() => {
    const persisted = stateMap[key];
    return mergePersisted(persisted, initialState);
  }, [stateMap, key, initialState]);

  const setState = useCallback(
    (updater) => {
      setPageState(key, (stored) => {
        const current =
          stored !== undefined ? mergePersisted(stored, initialState) : initialState;
        return typeof updater === 'function' ? updater(current) : updater;
      });
    },
    [key, setPageState, initialState],
  );

  const resetState = useCallback(() => {
    setPageState(key, initialState);
  }, [key, setPageState, initialState]);

  return [state, setState, resetState];
}

/**
 * Clear all persisted page state from localStorage and memory.
 * Components will re-render with their initial state.
 */
export function useClearPageState() {
  const ctx = useContext(PageStateContext);
  if (!ctx) {
    throw new Error('useClearPageState must be used within PageStateProvider');
  }
  const { setPageState } = ctx;

  return useCallback(() => {
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      // ignore
    }
    setPageState(() => ({}));
  }, [setPageState]);
}
