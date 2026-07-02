import { useEffect, useState } from 'react';
import { getQueryFields } from '../api/endpoints.js';

let cache = null;
let inflight = null;
const listeners = new Set();

function notify() {
  listeners.forEach((cb) => cb(cache));
}

async function load() {
  if (cache) return cache;
  if (inflight) return inflight;
  inflight = getQueryFields()
    .then((data) => {
      cache = normalize(data);
      notify();
      return cache;
    })
    .catch((err) => {
      inflight = null;
      throw err;
    });
  return inflight;
}

function normalize(raw) {
  const all = [];
  const byName = {};
  const sections = ['categorical', 'geometric', 'numeric', 'process'];
  sections.forEach((section) => {
    (raw[section] || []).forEach((f) => {
      const item = { ...f, section };
      all.push(item);
      byName[f.name] = item;
    });
  });
  return { raw, all, byName, numeric: raw.numeric || [], categorical: raw.categorical || [], process: raw.process || [], geometric: raw.geometric || [] };
}

export function displayLabel(field) {
  if (!field) return '';
  if (field.unit) return `${field.label} (${field.unit})`;
  return field.label || field.name;
}

export default function useFields() {
  const [state, setState] = useState({ data: cache, loading: !cache, error: null });

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
      .catch((err) => {
        if (alive) setState({ data: null, loading: false, error: err });
      });
    const cb = (data) => alive && setState({ data, loading: false, error: null });
    listeners.add(cb);
    return () => {
      alive = false;
      listeners.delete(cb);
    };
  }, []);

  return state;
}
