"""静态网页版数据快照导出（规格 §3）。

从桌面 sqlite（~/.aln-data/aln-data.db）导出 Parquet/JSON 到 frontend/public/webdata/。
只读源库；可选并入代理模型数据（任务 2）。

用法：
    uv run python scripts/export_web_snapshot.py \
        --db ~/.aln-data/aln-data.db --out ../frontend/public/webdata
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# devices 排除列：原文件路径（静态版无原文件查看）
DEVICE_EXCLUDE = {"s_param_path"}

DEVICE_COLS = [
    "id",
    "batch_id",
    "original_filename",
    "display_name",
    "mark",
    "wafer",
    "folder_name",
    "coord",
    "x",
    "y",
    "eg",
    "fl",
    "ag",
    "pf",
    "area_n",
    "area_um2",
    "fs_ghz",
    "fp_ghz",
    "zs_ohm",
    "zp_ohm",
    "qs",
    "qp",
    "qs_bodeq",
    "qp_bodeq",
    "dbqs",
    "dbqp",
    "bodeq_fitted",
    "bodeq_smooth",
    "bodeq_raw",
    "fbode_ghz",
    "k2eff_pct",
    "fp2_ghz",
    "fs2_ghz",
    "zp2_ohm",
    "zs2_ohm",
    "deembedded",
    "s_param_port",
]

# batches 列：file_path/raw_zip_path/source 是服务器路径，静态版无意义不导出
BATCH_COLS = [
    "id",
    "batch_no",
    "mapping_id",
    "f_start_ghz",
    "f_end_ghz",
    "deembedded",
    "deembed_method",
    "process_type",
    "uploaded_at",
    "uploaded_by",
    "device_count",
    "task_id",
]
ENTRY_COLS = [
    "id",
    "mapping_id",
    "mark",
    "description",
    "eg",
    "fl",
    "ag",
    "area_s11",
    "area_s22",
    "has_pf",
]


def _rows(conn: sqlite3.Connection, table: str, cols: list[str]) -> list[dict]:
    cur = conn.execute(f"SELECT {', '.join(cols)} FROM {table}")
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _write_arrow(df, path: Path) -> None:
    """Arrow IPC 文件，外层 gzip（path 应为 *.arrow.gz）。

    用 Arrow 而非 Parquet：DuckDB-WASM 的 parquet_scan 需联网下载
    parquet 扩展（extensions.duckdb.org，不可靠）；Arrow IPC 由前端
    apache-arrow 解码后 insertArrowTable 直接入库，完全自包含。
    注意 IPC 内不压缩：arrow-js 不支持 lz4/zstd record batch；
    外层 gzip 由浏览器 DecompressionStream 解压。
    """
    import gzip

    import pyarrow as pa

    table = pa.Table.from_pandas(df, preserve_index=False)
    # mtime=0：同内容 → 同字节（git diff 干净、缓存友好）
    with open(path, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                       compresslevel=6, mtime=0) as sink, pa.ipc.new_file(sink, table.schema) as w:
        w.write_table(table)


def export_all(db_path: str | Path, surrogate_dir: str | Path | None, out_dir: str | Path) -> dict:
    import pandas as pd  # 延迟导入：脚本级依赖

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    written: list[str] = []

    # devices.arrow：JOIN batches 拍平 batch_no
    cols = [c for c in DEVICE_COLS if c not in DEVICE_EXCLUDE]
    select_cols = ", ".join(f"d.{c}" for c in cols)
    df = pd.read_sql_query(
        f"SELECT {select_cols}, b.batch_no FROM devices d"
        " JOIN batches b ON d.batch_id = b.id ORDER BY d.id",
        conn,
    )
    _write_arrow(df, out / "devices.arrow.gz")
    written.append("devices.arrow.gz")

    # batches.json
    batches = _rows(conn, "batches", BATCH_COLS)
    (out / "batches.json").write_text(json.dumps(batches, ensure_ascii=False), encoding="utf-8")
    written.append("batches.json")

    # mappings.json（含 entries；entry_count 直接取表列）
    mappings = _rows(conn, "mappings", ["id", "name", "entry_count", "uploaded_at"])
    entries = _rows(conn, "mapping_entries", ENTRY_COLS)
    by_mid: dict[int, list[dict]] = {}
    for e in entries:
        by_mid.setdefault(e["mapping_id"], []).append(e)
    for m in mappings:
        m["entries"] = by_mid.get(m["id"], [])
    (out / "mappings.json").write_text(json.dumps(mappings, ensure_ascii=False), encoding="utf-8")
    written.append("mappings.json")

    # process.json（复用 api.process 的 STEPS 元数据；表不存在则跳过）
    from app.api.process import STEPS

    process: dict[str, dict] = {}
    for key, (model, label, fields) in STEPS.items():
        table = model.__tablename__
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue
        names = [f.name for f in fields]
        if "id" not in names:
            names = ["id", *names]
        rows = _rows(conn, table, names)
        process[key] = {
            "label": label,
            "fields": [f.model_dump() for f in fields],
            "rows": rows,
        }
    (out / "process.json").write_text(
        json.dumps(process, ensure_ascii=False, default=str), encoding="utf-8"
    )
    written.append("process.json")

    # stats.json（与 /api/stats 同键；静态版无任务/磁盘语义 → 0/null）
    n_dev = conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
    stats = {
        "batches": len(batches),
        "devices": n_dev,
        "mappings": len(mappings),
        "disk_used_gb": None,
        "disk_free_gb": None,
        "tasks_pending": 0,
        "tasks_running": 0,
    }
    (out / "stats.json").write_text(json.dumps(stats), encoding="utf-8")
    written.append("stats.json")

    # fields.json（精确镜像后端 /api/query/fields 的硬编码结构；
    # values_endpoint 仅作"可取 distinct"标记，前端实际调 distinctValues(field)）
    fields = {
        "categorical": [
            {
                "name": "batch_no",
                "label": "批次号",
                "values_endpoint": "/api/query/distinct?field=batch_no",
            },
            {
                "name": "wafer",
                "label": "晶圆 (Wafer)",
                "values_endpoint": "/api/query/distinct?field=wafer",
            },
            {"name": "pf", "label": "PF", "values": ["Y", "N"]},
            {"name": "folder_name", "label": "端口 (S11/S22)", "values": ["S11", "S22"]},
            {
                "name": "original_filename",
                "label": "原始文件名",
                "values_endpoint": "/api/query/distinct?field=original_filename",
            },
            {
                "name": "display_name",
                "label": "展示名",
                "values_endpoint": "/api/query/distinct?field=display_name",
            },
            {"name": "mark", "label": "代号", "values_endpoint": "/api/query/distinct?field=mark"},
            {
                "name": "coord",
                "label": "坐标",
                "values_endpoint": "/api/query/distinct?field=coord",
            },
            {
                "name": "area_n",
                "label": "区域编号",
                "values_endpoint": "/api/query/distinct?field=area_n",
            },
        ],
        "geometric": [
            {"name": "x", "label": "X 坐标"},
            {"name": "y", "label": "Y 坐标"},
        ],
        "numeric": [
            {"name": "fs_ghz", "label": "fs(GHz)", "unit": ""},
            {"name": "fp_ghz", "label": "fp(GHz)", "unit": ""},
            {"name": "zs_ohm", "label": "Zs", "unit": "Ω"},
            {"name": "zp_ohm", "label": "Zp", "unit": "Ω"},
            {"name": "qs", "label": "Qs", "unit": ""},
            {"name": "qp", "label": "Qp", "unit": ""},
            {"name": "qs_bodeq", "label": "bodeQs", "unit": ""},
            {"name": "qp_bodeq", "label": "bodeQp", "unit": ""},
            {"name": "dbqs", "label": "dBQs", "unit": "dB"},
            {"name": "dbqp", "label": "dBQp", "unit": "dB"},
            {"name": "bodeq_fitted", "label": "BodeQ 拟合", "unit": ""},
            {"name": "bodeq_smooth", "label": "BodeQ 平滑", "unit": ""},
            {"name": "bodeq_raw", "label": "BodeQ 原始", "unit": ""},
            {"name": "fbode_ghz", "label": "fBode(GHz)", "unit": ""},
            {"name": "k2eff_pct", "label": "k²eff(%)", "unit": ""},
            {"name": "fp2_ghz", "label": "fp2(GHz)", "unit": ""},
            {"name": "fs2_ghz", "label": "fs2(GHz)", "unit": ""},
            {"name": "zp2_ohm", "label": "二次并联阻抗 (Zp2)", "unit": "Ω"},
            {"name": "zs2_ohm", "label": "二次串联阻抗 (Zs2)", "unit": "Ω"},
        ],
        "process": [
            {"name": "eg", "label": "EG", "values_endpoint": "/api/query/distinct?field=eg"},
            {"name": "fl", "label": "FL", "values_endpoint": "/api/query/distinct?field=fl"},
            {"name": "ag", "label": "AG", "values_endpoint": "/api/query/distinct?field=ag"},
            {
                "name": "area_um2",
                "label": "面积",
                "unit": "",
                "values_endpoint": "/api/query/distinct?field=area_um2",
            },
        ],
    }
    (out / "fields.json").write_text(json.dumps(fields, ensure_ascii=False), encoding="utf-8")
    written.append("fields.json")

    missing: list[str] = []
    if surrogate_dir is not None:
        from export_web_surrogate_data import export_surrogate_webdata

        w, m = export_surrogate_webdata(Path(surrogate_dir), out)
        written += w
        missing += m

    manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "files": written,
        "missing": missing,
        "counts": {"devices": int(n_dev), "batches": len(batches)},
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    conn.close()
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path.home() / ".aln-data/aln-data.db"))
    ap.add_argument("--surrogate-dir", default=str(PROJECT_ROOT / "surrogate_data"))
    ap.add_argument("--out", default=str(PROJECT_ROOT.parent / "frontend/public/webdata"))
    args = ap.parse_args()
    m = export_all(args.db, args.surrogate_dir, args.out)
    print(json.dumps(m["counts"], ensure_ascii=False), "→", args.out)


if __name__ == "__main__":
    main()
