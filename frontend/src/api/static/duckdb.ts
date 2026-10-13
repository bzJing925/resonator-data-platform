/** DuckDB-WASM 懒单例 + 按需注册 Arrow 表（规格 §4）。
 *  devices.arrow 在首个数据查询时加载；cell_means.arrow 仅代理模型页需要。
 *  用 Arrow IPC 而非 Parquet：parquet_scan 需联网下载 parquet 扩展
 *  （extensions.duckdb.org，不可靠且违背静态自包含）；Arrow 由
 *  apache-arrow 在 JS 侧解码后 insertArrowTable 入库，完全离线。 */

import * as duckdb from '@duckdb/duckdb-wasm';
import { tableFromIPC } from 'apache-arrow';
import duckdb_wasm from '@duckdb/duckdb-wasm/dist/duckdb-mvp.wasm?url';
import mvp_worker from '@duckdb/duckdb-wasm/dist/duckdb-browser-mvp.worker.js?url';

let _db: Promise<duckdb.AsyncDuckDB> | null = null;
let _conn: Promise<duckdb.AsyncDuckDBConnection> | null = null;
let snapshotRoot = 'webdata/';
export function configureSnapshotRoot(url: string) { snapshotRoot = url; }
let devicesSnapshot: string | null | undefined;
export async function publishedSnapshotToken(): Promise<string | null> {
  const response = await fetch(`${snapshotRoot}manifest.json`, { cache: 'no-store' });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error('无法核对已发布数据版本');
  return JSON.stringify(await response.json());
}
export async function assertDeviceSnapshot() {
  if (devicesSnapshot !== undefined && await publishedSnapshotToken() !== devicesSnapshot) {
    throw new Error('QUERY_SOURCE_CHANGED：已发布数据版本变化，请重新加载页面');
  }
}
export function deviceSnapshotMetadata(): unknown {
  return devicesSnapshot ? JSON.parse(devicesSnapshot) : null;
}
const _tables = new Map<string, Promise<void>>();

async function init(): Promise<duckdb.AsyncDuckDB> {
  // 只打 MVP bundle：eh 变体多 36MB wasm，MVP 全浏览器可用
  const bundle = await duckdb.selectBundle({
    mvp: { mainModule: duckdb_wasm, mainWorker: mvp_worker },
  });
  const worker = new Worker(bundle.mainWorker!);
  const db = new duckdb.AsyncDuckDB(
    new duckdb.ConsoleLogger(duckdb.LogLevel.WARNING),
    worker,
  );
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
  return db;
}

function getDb(): Promise<duckdb.AsyncDuckDB> {
  if (!_db) _db = init();
  return _db;
}

export function getConn(): Promise<duckdb.AsyncDuckDBConnection> {
  if (!_conn) _conn = getDb().then((db) => db.connect());
  return _conn;
}

/** 注册某张 Arrow 表（幂等）。文件为 Arrow IPC + 外层 gzip
 *  （arrow-js 不支持 IPC 内压缩；GH Pages 无法自定义 Content-Encoding，
 *  故用浏览器 DecompressionStream 手动解压）。 */
export function ensureTable(name: 'devices' | 'cell_means'): Promise<void> {
  let p = _tables.get(name);
  if (!p) {
    p = (async () => {
      const conn = await getConn();
      const snapshot = name === 'devices' ? await publishedSnapshotToken() : undefined;
      const resp = await fetch(`${snapshotRoot}${name}.arrow.gz`);
      if (!resp.ok) throw new Error(`快照缺失: ${name}.arrow.gz`);
      const received = await resp.arrayBuffer();
      const header = new Uint8Array(received);
      const gzipped = header[0] === 0x1f && header[1] === 0x8b;
      if (name === 'devices' && snapshot) {
        const metadata = JSON.parse(snapshot);
        const expected = gzipped ? metadata.devices_sha256 : metadata.devices_arrow_sha256;
        if (Array.isArray(metadata.comparison_sources) && metadata.comparison_sources.length && typeof expected !== 'string') throw new Error('静态传输缺少对应字节的发布摘要');
        if (typeof expected === 'string') {
          const bytes = await crypto.subtle.digest('SHA-256', received);
          const digest = [...new Uint8Array(bytes)].map((v) => v.toString(16).padStart(2, '0')).join('');
          if (digest !== expected) throw new Error('QUERY_SOURCE_CHANGED：静态数据文件与发布证据不一致');
        }
      }
      const ds = new DecompressionStream('gzip');
      // fetch may already decode HTTP Content-Encoding:gzip (e.g. Vite preview/Nginx).
      const buf = gzipped ? await new Response(new Blob([received]).stream().pipeThrough(ds)).arrayBuffer() : received;
      const table = tableFromIPC(new Uint8Array(buf));
      if (name === 'devices' && await publishedSnapshotToken() !== snapshot) throw new Error('QUERY_SOURCE_CHANGED：加载数据期间版本变化');
      await conn.insertArrowTable(table, { name, create: true });
      if (name === 'devices') devicesSnapshot = snapshot;
    })();
    _tables.set(name, p);
  }
  return p;
}

/** SQL 字符串字面量转义（值来自 UI 输入）。 */
export function q(v: string | number): string {
  if (typeof v === 'number') return Number.isFinite(v) ? String(v) : 'NULL';
  return `'${String(v).replaceAll("'", "''")}'`;
}

/** DuckDB BIGINT → BigInt 的 JSON 兼容化。 */
export function normalizeRow(r: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(r)) {
    out[k] = typeof v === 'bigint' ? Number(v) : v;
  }
  return out;
}
