"""主机导出与 canonical 数据仓的双向合并（build_web.py 调用）。

动机：同事分享包经由 aln-data-web CI 直接进入 canonical（data/），主机 sqlite
未必有这些批次的原始数据。若 build_web.py 只用 sqlite 导出直接覆盖，同事批次会被抹掉。

规则：
- 主机 sqlite 导出的批次优先（权威，全字段保真）；
- canonical 中 sqlite 没有的批次（同事贡献）整批追加（重编 batch id 防冲突）；
- 合并结果同时写回 webdata（部署用）与 canonical（推送回 aln-data-web main）。

用法：
    uv run python scripts/sync_canonical_webdata.py \
        --webdata ../frontend/public/webdata --canonical ../.web-data
"""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

SURR_FILES = ["baseline.json", "surr_meta.json", "cell_means.arrow.gz",
              "stage1.onnx", "stage2.onnx"]


def _read_arrow_gz(path: Path):
    import pyarrow as pa

    with gzip.open(path, "rb") as f:
        return pa.ipc.open_file(pa.py_buffer(f.read())).read_all().to_pandas()


def _write_arrow_gz(df, path: Path) -> None:
    import pyarrow as pa

    table = pa.Table.from_pandas(df, preserve_index=False)
    with open(path, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                           compresslevel=6, mtime=0) as sink:
            with pa.ipc.new_file(sink, table.schema) as w:
                w.write_table(table)


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def sync_canonical(webdata: Path, canonical: Path) -> dict:
    """返回合并报告。canonical 不存在时视为空（纯主机数据）。"""
    import pandas as pd

    webdata = Path(webdata)
    canonical = Path(canonical)
    meta_dir = canonical / "data/meta"

    df_e = _read_arrow_gz(webdata / "devices.arrow.gz")
    batches_e = _load_json(webdata / "batches.json")
    mappings_e = _load_json(webdata / "mappings.json")
    known = {b["batch_no"] for b in batches_e}

    appended: list[str] = []
    can_dev = canonical / "data/devices.arrow.gz"
    if can_dev.exists():
        df_c = _read_arrow_gz(can_dev)
        batches_c = _load_json(meta_dir / "batches.json")
        mappings_c = _load_json(meta_dir / "mappings.json")
        extra = [b for b in batches_c if b["batch_no"] not in known]
        if extra:
            map_id_by_name_e = {m["name"]: m["id"] for m in mappings_e}
            next_bid = max((b["id"] for b in batches_e), default=0)
            next_mid = max((m["id"] for m in mappings_e), default=0)
            next_eid = max(
                (e["id"] for m in mappings_e for e in m.get("entries", [])), default=0
            )
            parts = [df_e]
            for b in extra:
                # 对照表按名字对齐；canonical 独有的对照表随批次并入
                mc = next((m for m in mappings_c if m["id"] == b.get("mapping_id")), None)
                if mc is None:
                    mapping_id = None
                elif mc["name"] in map_id_by_name_e:
                    mapping_id = map_id_by_name_e[mc["name"]]
                else:
                    next_mid += 1
                    entries = []
                    for e in mc.get("entries", []):
                        next_eid += 1
                        entries.append({**e, "id": next_eid, "mapping_id": next_mid})
                    mappings_e.append(
                        {**mc, "id": next_mid, "entries": entries,
                         "entry_count": len(entries)}
                    )
                    map_id_by_name_e[mc["name"]] = next_mid
                    mapping_id = next_mid
                next_bid += 1
                rows = df_c[df_c["batch_no"] == b["batch_no"]].copy()
                rows["batch_id"] = next_bid
                parts.append(rows)
                nb = dict(b)
                nb["id"] = next_bid
                nb["mapping_id"] = mapping_id
                batches_e.append(nb)
                appended.append(b["batch_no"])
            df_e = pd.concat(parts, ignore_index=True)

    stats = {
        "batches": len(batches_e),
        "devices": int(len(df_e)),
        "mappings": len(mappings_e),
        "disk_used_gb": None,
        "disk_free_gb": None,
        "tasks_pending": 0,
        "tasks_running": 0,
    }

    # 写回 webdata（部署用）
    _write_arrow_gz(df_e, webdata / "devices.arrow.gz")
    _write_json(webdata / "batches.json", batches_e)
    _write_json(webdata / "mappings.json", mappings_e)
    _write_json(webdata / "stats.json", stats)
    manifest = _load_json(webdata / "manifest.json")
    manifest["generated_at"] = datetime.now(UTC).isoformat()
    manifest["counts"] = {"devices": int(len(df_e)), "batches": len(batches_e)}
    _write_json(webdata / "manifest.json", manifest)

    # 写回 canonical（推送回 aln-data-web main）
    (canonical / "data").mkdir(parents=True, exist_ok=True)
    meta_dir.mkdir(parents=True, exist_ok=True)
    _write_arrow_gz(df_e, can_dev)
    for name, obj in [("batches", batches_e), ("mappings", mappings_e), ("stats", stats)]:
        _write_json(meta_dir / f"{name}.json", obj)
    for name in ("process", "fields"):
        src = webdata / f"{name}.json"
        if src.exists():
            shutil.copy(src, meta_dir / f"{name}.json")
    (canonical / "surr").mkdir(exist_ok=True)
    for name in SURR_FILES:
        src = webdata / name
        if src.exists():
            shutil.copy(src, canonical / "surr" / name)

    return {"devices": int(len(df_e)), "batches": len(batches_e), "appended": appended}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--webdata", default=str(PROJECT_ROOT.parent / "frontend/public/webdata"))
    ap.add_argument("--canonical", default=str(PROJECT_ROOT.parent / ".web-data"))
    args = ap.parse_args()
    report = sync_canonical(args.webdata, args.canonical)
    if report["appended"]:
        print(f"警告：以下批次来自同事分享包、本地 sqlite 没有（已用 canonical 版本补齐）: "
              f"{report['appended']}；建议拿到原始 zip 后本地入库")
    print(f"同步完成: devices={report['devices']} batches={report['batches']}")


if __name__ == "__main__":
    main()
