import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

// vitest 未开 globals，RTL 自动 cleanup 不生效，这里显式注册
afterEach(cleanup);

// Node ≥22 会在 globalThis 上注册一个不可用的 localStorage 占位访问器
// （未传 --localstorage-file 时访问即缺失），遮蔽 jsdom 的实现。
// 检测后统一替换为内存 Storage，保证被测代码与测试都能正常使用。
if (typeof globalThis.localStorage?.clear !== 'function') {
  const store = new Map<string, string>();
  const memory: Storage = {
    get length() { return store.size; },
    clear: () => store.clear(),
    getItem: (key) => (store.has(key) ? store.get(key)! : null),
    key: (index) => [...store.keys()][index] ?? null,
    removeItem: (key) => { store.delete(key); },
    setItem: (key, value) => { store.set(key, String(value)); },
  };
  Object.defineProperty(globalThis, 'localStorage', { value: memory, configurable: true });
}
