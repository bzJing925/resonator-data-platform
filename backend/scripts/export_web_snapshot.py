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
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# devices 排除列：原文件路径（静态版无原文件查看）
DEVICE_EXCLUDE = {"s_param_path"}

DEVICE_COLS = [
    "id", "batch_id", "original_filename", "display_name", "mark", "wafer", "folder_name",
    "coord", "x", "y", "eg", "fl", "ag", "pf", "area_n", "area_um2",
    "fs_ghz", "fp_ghz", "zs_ohm", "zp_ohm", "qs", "qp", "qs_bodeq", "qp_bodeq",
    "dbqs", "dbqp", "bodeq_fitted", "bodeq_smooth", "bodeq_raw", "fbode_ghz",
    "k2eff_pct", "fp2_ghz", "fs2_ghz", "zp2_ohm", "zs2_ohm", "deembedded", "s_param_port",
]

BATCH_COLS = ["id", "batch_no", "mapping_id", "f_start_ghz", "f_end_ghz",
              "deembedded", "uploaded_at", "device_count"]
ENTRY_COLS = ["id", "mapping_id", "mark", "description", "eg", "fl", "ag",
              "area_s11", "area_s22", "has_pf"]


def _rows(conn: sqlite3.Connection, table: str, cols: list[str]) -> list[dict]:
    cur = conn.execute(f"SELECT {', '.join(cols)} FROM {table}")
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def export_all(db_path: str | Path, surrogate_dir: str | Path | None,
               out_dir: str | Path) -> dict:
    import pandas as pd  # 延迟导入：脚本级依赖

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    written: list[str] = []

    # devices.parquet：JOIN batches 拍平 batch_no
    cols = [c for c in DEVICE_COLS if c not in DEVICE_EXCLUDE]
    select_cols = ", ".join(f"d.{c}" for c in cols)
    df = pd.read_sql_query(
        f"SELECT {select_cols}, b.batch_no FROM devices d"
        " JOIN batches b ON d.batch_id = b.id ORDER BY d.id",
        conn,
    )
    df.to_parquet(out / "devices.parquet", compression="zstd", index=False)
    written.append("devices.parquet")

    # batches.json
    batches = _rows(conn, "batches", BATCH_COLS)
    (out / "batches.json").write_text(
        json.dumps(batches, ensure_ascii=False), encoding="utf-8")
    written.append("batches.json")

    # mappings.json（含 entries）
    mappings = _rows(conn, "mappings", ["id", "name", "uploaded_at"])
    entries = _rows(conn, "mapping_entries", ENTRY_COLS)
    by_mid: dict[int, list[dict]] = {}
    for e in entries:
        by_mid.setdefault(e["mapping_id"], []).append(e)
    for m in mappings:
        m["entries"] = by_mid.get(m["id"], [])
        m["entry_count"] = len(m["entries"])
    (out / "mappings.json").write_text(
        json.dumps(mappings, ensure_ascii=False), encoding="utf-8")
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
        rows = _rows(conn, table, ["id", *names]) if "id" not in names else _rows(conn, table, names)
        process[key] = {
            "label": label,
            "fields": [f.model_dump() for f in fields],
            "rows": rows,
        }
    (out / "process.json").write_text(
        json.dumps(process, ensure_ascii=False, default=str), encoding="utf-8")
    written.append("process.json")

    # stats.json（对齐 /api/stats 的核心字段）
    n_dev = conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
    n_pass = conn.execute("SELECT COUNT(*) FROM devices WHERE pf='Y'").fetchone()[0]
    stats = {
        "total_batches": len(batches),
        "total_devices": n_dev,
        "pass_devices": n_pass,
        "pass_rate": (n_pass / n_dev) if n_dev else 0.0,
    }
    (out / "stats.json").write_text(json.dumps(stats), encoding="utf-8")
    written.append("stats.json")

    # fields.json（/api/query/fields 的静态等价）
    fields = {
        "categorical": [
            {"name": "batch_no", "label": "批次号", "values": sorted({b["batch_no"] for b in batches})},
            {"name": "wafer", "label": "Wafer"},
            {"name": "pf", "label": "Pass/Fail", "values": ["Y", "N"]},
            {"name": "folder_name", "label": "端口", "values": ["S11", "S22"]},
        ],
        "geometric": [
            {"name": "x", "label": "X 坐标"}, {"name": "y", "label": "Y 坐标"},
        ],
        "numeric": [
            {"name": n, "label": n} for n in cols
            if n not in ("id", "batch_id", "original_filename", "display_name", "mark",
                         "coord", "folder_name", "pf", "deembedded", "s_param_port")
        ],
        "process": [
            {"name": "eg", "label": "EG"}, {"name": "fl", "label": "FL"},
            {"name": "ag", "label": "AG"},
        ],
    }
    (out / "fields.json").write_text(
        json.dumps(fields, ensure_ascii=False), encoding="utf-8")
    written.append("fields.json")

    missing: list[str] = []
    if surrogate_dir is not None:
        from export_web_surrogate_data import export_surrogate_webdata

        w, m = export_surrogate_webdata(Path(surrogate_dir), out)
        written += w
        missing += m

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": written,
        "missing": missing,
        "counts": {"devices": int(n_dev), "batches": len(batches)},
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
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
