/** DuckDB-WASM 懒单例 + 按需注册 Parquet 视图（规格 §4）。
 *  devices.parquet 在首个数据查询时加载；cell_means.parquet 仅代理模型页需要。 */

import * as duckdb from '@duckdb/duckdb-wasm';
import duckdb_wasm from '@duckdb/duckdb-wasm/dist/duckdb-mvp.wasm?url';
import mvp_worker from '@duckdb/duckdb-wasm/dist/duckdb-browser-mvp.worker.js?url';
import duckdb_wasm_eh from '@duckdb/duckdb-wasm/dist/duckdb-eh.wasm?url';
import eh_worker from '@duckdb/duckdb-wasm/dist/duckdb-browser-eh.worker.js?url';

let _db: Promise<duckdb.AsyncDuckDB> | null = null;
let _conn: Promise<duckdb.AsyncDuckDBConnection> | null = null;
const _tables = new Map<string, Promise<void>>();

async function init(): Promise<duckdb.AsyncDuckDB> {
  const bundle = await duckdb.selectBundle({
    mvp: { mainModule: duckdb_wasm, mainWorker: mvp_worker },
    eh: { mainModule: duckdb_wasm_eh, mainWorker: eh_worker },
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

/** 注册某张 Parquet 为同名视图（幂等）。 */
export function ensureTable(name: 'devices' | 'cell_means'): Promise<void> {
  let p = _tables.get(name);
  if (!p) {
    p = (async () => {
      const db = await getDb();
      const conn = await getConn();
      const buf = await fetch(`webdata/${name}.parquet`).then((r) => {
        if (!r.ok) throw new Error(`快照缺失: ${name}.parquet`);
        return r.arrayBuffer();
      });
      await db.registerFileBuffer(`${name}.parquet`, new Uint8Array(buf));
      await conn.query(
        `CREATE VIEW ${name} AS SELECT * FROM parquet_scan('${name}.parquet')`,
      );
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
