import { useEffect, useState } from 'react';
import { getQueryFields } from '../api/endpoints';
import type { FieldMeta, FieldsData } from '../types';

let cache: FieldsData | null = null;
let inflight: Promise<FieldsData> | null = null;
const listeners = new Set<(data: FieldsData | null) => void>();

function notify(data: FieldsData | null) {
  listeners.forEach((cb) => cb(data));
}

async function load(): Promise<FieldsData> {
  if (cache) return cache;
  if (inflight) return inflight;
  inflight = getQueryFields()
    .then((data: Record<string, FieldMeta[]>) => {
      cache = normalize(data);
      notify(cache);
      return cache;
    })
    .catch((err) => {
      inflight = null;
      throw err;
    });
  return inflight;
}

function normalize(raw: Record<string, FieldMeta[]>): FieldsData {
  const all: FieldMeta[] = [];
  const byName: Record<string, FieldMeta> = {};
  const sections = ['categorical', 'geometric', 'numeric', 'process'] as const;
  sections.forEach((section) => {
    (raw[section] || []).forEach((f) => {
      const item = { ...f, section };
      all.push(item);
      byName[f.name] = item;
    });
  });
  return {
    raw,
    all,
    byName,
    numeric: raw.numeric || [],
    categorical: raw.categorical || [],
    process: raw.process || [],
    geometric: raw.geometric || [],
  };
}

export function displayLabel(field?: FieldMeta | null): string {
  if (!field) return '';
  if (field.unit) return `${field.label} (${field.unit})`;
  return field.label || field.name;
}

interface UseFieldsState {
  data: FieldsData | null;
  loading: boolean;
  error: Error | null;
}

export default function useFields(): UseFieldsState {
  const [state, setState] = useState<UseFieldsState>({
    data: cache,
    loading: !cache,
    error: null,
  });

  useEffect(() => {
    let alive = true;
    if (cache) {
      setState({ data: cache, loading: false, error: null });
      return;
    }
    setState((s) => ({ ...s, loading: true }));
    load()
      .then((data) => {
        if (alive) setState({ data, loading: false, error: null });
      })
      .catch((err: Error) => {
        if (alive) setState({ data: null, loading: false, error: err });
      });
    const cb = (data: FieldsData | null) => alive && setState({ data, loading: false, error: null });
    listeners.add(cb);
    return () => {
      alive = false;
      listeners.delete(cb);
    };
  }, []);

  return state;
}
