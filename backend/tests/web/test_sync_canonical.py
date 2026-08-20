"""canonical 双向合并测试：主机导出 + 同事批次 → 合并契约。"""

import gzip
import json
import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from sync_canonical_webdata import sync_canonical  # noqa: E402


def _write_arrow_gz(df: pd.DataFrame, path: Path) -> None:
    table = pa.Table.from_pandas(df, preserve_index=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(str(path), "wb", compresslevel=6) as sink:
        with pa.ipc.new_file(sink, table.schema) as w:
            w.write_table(table)


def _read_rows(path: Path) -> pd.DataFrame:
    with gzip.open(path, "rb") as f:
        return pa.ipc.open_file(pa.py_buffer(f.read())).read_all().to_pandas()


def _device_rows(batch_id: int, batch_no: str, n: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "id": range(1, n + 1),
            "batch_id": [batch_id] * n,
            "batch_no": [batch_no] * n,
            "original_filename": [f"f{i}.s1p" for i in range(n)],
            "fs_ghz": [4.8 + i * 0.01 for i in range(n)],
            "fp_ghz": [5.1 + i * 0.01 for i in range(n)],
            "pf": ["N"] * n,
        }
    )


def _make_webdata(root: Path, batch_nos: list[str]) -> Path:
    """模拟主机 sqlite 导出的 webdata。"""
    webdata = root / "webdata"
    webdata.mkdir(parents=True)
    parts = [_device_rows(i + 1, bn, 2) for i, bn in enumerate(batch_nos)]
    df = pd.concat(parts, ignore_index=True)
    df["id"] = range(1, len(df) + 1)
    _write_arrow_gz(df, webdata / "devices.arrow.gz")
    batches = [
        {"id": i + 1, "batch_no": bn, "mapping_id": 1, "device_count": 2,
         "f_start_ghz": None, "f_end_ghz": None, "deembedded": False,
         "deembed_method": None, "process_type": "S2P", "uploaded_at": "",
         "uploaded_by": "", "task_id": None}
        for i, bn in enumerate(batch_nos)
    ]
    mappings = [{"id": 1, "name": "ELB003", "entry_count": 0, "uploaded_at": "", "entries": []}]
    (webdata / "batches.json").write_text(json.dumps(batches), encoding="utf-8")
    (webdata / "mappings.json").write_text(json.dumps(mappings), encoding="utf-8")
    (webdata / "stats.json").write_text("{}", encoding="utf-8")
    (webdata / "manifest.json").write_text(
        json.dumps({"generated_at": "", "files": [], "missing": [],
                    "counts": {"devices": len(df), "batches": len(batches)}}),
        encoding="utf-8",
    )
    return webdata


def test_sync_appends_colleague_batches(tmp_path):
    webdata = _make_webdata(tmp_path, ["#2", "01"])
    # canonical 多一个同事批次 #T1（映射到已有对照表 ELB003）
    canonical = tmp_path / "canonical"
    df_c = _device_rows(3, "#T1", 3)
    df_c["id"] = [10, 11, 12]
    _write_arrow_gz(df_c, canonical / "data/devices.arrow.gz")
    (canonical / "data/meta").mkdir(parents=True)
    (canonical / "data/meta/batches.json").write_text(json.dumps([
        {"id": 1, "batch_no": "#2", "mapping_id": 1, "device_count": 2},
        {"id": 2, "batch_no": "01", "mapping_id": 1, "device_count": 2},
        {"id": 3, "batch_no": "#T1", "mapping_id": 1, "device_count": 3},
    ]), encoding="utf-8")
    (canonical / "data/meta/mappings.json").write_text(
        json.dumps([{"id": 1, "name": "ELB003", "entry_count": 0, "uploaded_at": "",
                     "entries": []}]), encoding="utf-8")

    report = sync_canonical(webdata, canonical)
    assert report["appended"] == ["#T1"]
    assert report["devices"] == 7 and report["batches"] == 3

    df = _read_rows(webdata / "devices.arrow.gz")
    assert set(df["batch_no"]) == {"#2", "01", "#T1"}
    # 重编 batch_id：#T1 → 3（续主机最大 id=2），不与 canonical 原 id 冲突
    batches = json.loads((webdata / "batches.json").read_text())
    t1 = next(b for b in batches if b["batch_no"] == "#T1")
    assert t1["id"] == 3 and t1["mapping_id"] == 1
    assert set(df[df["batch_no"] == "#T1"]["batch_id"]) == {3}
    # canonical 已写回同一结果
    df_c2 = _read_rows(canonical / "data/devices.arrow.gz")
    assert len(df_c2) == 7


def test_sync_noop_without_canonical(tmp_path):
    webdata = _make_webdata(tmp_path, ["#2"])
    canonical = tmp_path / "canonical"  # 不存在
    report = sync_canonical(webdata, canonical)
    assert report["appended"] == [] and report["devices"] == 2
    # canonical 被创建并写回
    assert (canonical / "data/devices.arrow.gz").exists()


def test_sync_host_wins_on_overlap(tmp_path):
    """同 batch_no 时主机 sqlite 版本为准（canonical 重复行被丢弃）。"""
    webdata = _make_webdata(tmp_path, ["#2"])
    canonical = tmp_path / "canonical"
    df_c = _device_rows(5, "#2", 5)  # canonical 的 #2 有 5 行（旧/重复）
    _write_arrow_gz(df_c, canonical / "data/devices.arrow.gz")
    (canonical / "data/meta").mkdir(parents=True)
    (canonical / "data/meta/batches.json").write_text(
        json.dumps([{"id": 1, "batch_no": "#2", "mapping_id": 1, "device_count": 5}]),
        encoding="utf-8")
    (canonical / "data/meta/mappings.json").write_text(
        json.dumps([{"id": 1, "name": "ELB003", "entries": []}]), encoding="utf-8")

    report = sync_canonical(webdata, canonical)
    assert report["appended"] == []
    df = _read_rows(webdata / "devices.arrow.gz")
    assert len(df) == 2  # 主机版本（2 行）胜出
