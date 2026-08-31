import { useEffect, useState } from 'react';
import { getQueryFields } from '../api/endpoints.js';

let cache = null;
let inflight = null;
const listeners = new Set();

function notify() {
  listeners.forEach((cb) => cb(cache));
}

function errorMessage(err) {
  if (err.response) {
    const status = err.response.status;
    const detail = err.response.data?.detail;
    if (detail) return detail;
    // Vite 开发服务器代理后端失败时，常返回空 body 的 500
    if (status === 500) return '无法连接到后端服务，请确认 uvicorn 已启动并在正确端口运行';
    return `请求失败 (${status})`;
  }
  if (err.message === 'Network Error') {
    return '网络错误：后端服务未启动或已断开';
  }
  return err.message || '字段加载失败';
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function load(attempt = 0) {
  if (cache) return cache;
  if (inflight) return inflight;
  inflight = getQueryFields()
    .then((data) => {
      cache = normalize(data);
      notify();
      return cache;
    })
    .catch(async (err) => {
      // 后端暂时不可用时自动重试最多 2 次
      if (!err.response && attempt < 2) {
        inflight = null;
        await sleep(500 * (attempt + 1));
        return load(attempt + 1);
      }
      inflight = null;
      const message = errorMessage(err);
      throw new Error(message);
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
