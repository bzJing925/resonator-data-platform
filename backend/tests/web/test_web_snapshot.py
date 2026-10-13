"""快照导出测试：小 sqlite → arrow.gz/json 契约。"""

import gzip
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from export_web_snapshot import export_all  # noqa: E402
from export_web_surrogate_data import export_surrogate_webdata  # noqa: E402


def _read_arrow_gz(path: Path):
    with gzip.open(path, "rb") as f:
        return pa.ipc.open_file(pa.py_buffer(f.read())).read_all()


DEVICE_COLS = (
    "id,batch_id,original_filename,display_name,mark,wafer,folder_name,coord,x,y,"
    "eg,fl,ag,pf,area_n,area_um2,fs_ghz,fp_ghz,zs_ohm,zp_ohm,qs,qp,qs_bodeq,qp_bodeq,"
    "dbqs,dbqp,bodeq_fitted,bodeq_smooth,bodeq_raw,fbode_ghz,k2eff_pct,fp2_ghz,fs2_ghz,"
    "zp2_ohm,zs2_ohm,deembedded,s_param_path,s_param_port"
)


@pytest.fixture()
def mini_db(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE batches (id INTEGER PRIMARY KEY, batch_no TEXT UNIQUE NOT NULL,"
        " mapping_id INTEGER, f_start_ghz REAL, f_end_ghz REAL, deembedded INTEGER DEFAULT 0,"
        " deembed_method TEXT, process_type TEXT, uploaded_at TEXT DEFAULT '',"
        " uploaded_by TEXT DEFAULT '', device_count INTEGER DEFAULT 0, task_id INTEGER)"
    )
    conn.execute(
        "CREATE TABLE mappings (id INTEGER PRIMARY KEY, name TEXT, file_path TEXT,"
        " entry_count INTEGER DEFAULT 0, uploaded_at TEXT DEFAULT '')"
    )
    conn.execute(
        "CREATE TABLE mapping_entries (id INTEGER PRIMARY KEY, mapping_id INTEGER,"
        " mark TEXT, description TEXT, eg REAL, fl REAL, ag REAL,"
        " area_s11 INTEGER, area_s22 INTEGER, has_pf INTEGER DEFAULT 0)"
    )
    conn.execute(f"CREATE TABLE devices ({', '.join(c + ' TEXT' for c in DEVICE_COLS.split(','))})")
    conn.execute(
        "INSERT INTO batches VALUES (1, '#T', 1, NULL, NULL, 0, NULL, NULL,"
        " '2026-01-01', '', 2, NULL)"
    )
    conn.execute("INSERT INTO mappings VALUES (1, 'M1', NULL, 1, '')")
    conn.execute(
        "INSERT INTO mapping_entries VALUES (1, 1, 'A1', 'EG1', 1.0, 0.5, NULL, 700, 5500, 0)"
    )
    for i in (1, 2):
        vals = {c: None for c in DEVICE_COLS.split(",")}
        vals.update(
            {
                "id": i,
                "batch_id": 1,
                "original_filename": f"f{i}.s1p",
                "mark": "A1",
                "eg": 1.0,
                "fl": 0.5,
                "area_um2": 700,
                "pf": "N",
                "fs_ghz": 4.8 + i * 0.001,
                "fp_ghz": 5.1,
                "k2eff_pct": 7.0,
                "qs": 500,
            }
        )
        conn.execute(
            f"INSERT INTO devices VALUES ({', '.join('?' for _ in DEVICE_COLS.split(','))})",
            [vals[c] for c in DEVICE_COLS.split(",")],
        )
    conn.commit()
    conn.close()
    return db


def test_export_all(mini_db, tmp_path):
    out = tmp_path / "webdata"
    export_all(mini_db, surrogate_dir=None, out_dir=out)
    t = _read_arrow_gz(out / "devices.arrow.gz")
    assert t.num_rows == 2
    assert "batch_no" in t.column_names and "s_param_path" not in t.column_names
    assert t.column("batch_no").to_pylist() == ["#T", "#T"]
    batches = json.loads((out / "batches.json").read_text())
    assert batches[0]["batch_no"] == "#T"
    maps = json.loads((out / "mappings.json").read_text())
    assert maps[0]["entries"][0]["mark"] == "A1"
    stats = json.loads((out / "stats.json").read_text())
    assert stats["devices"] == 2 and stats["batches"] == 1 and stats["mappings"] == 1
    fields = json.loads((out / "fields.json").read_text())
    assert any(f["name"] == "batch_no" for f in fields["categorical"])
    assert any(f["name"] == "fs_ghz" for f in fields["numeric"])
    manifest = json.loads((out / "manifest.json").read_text())
    assert "devices.arrow.gz" in manifest["files"]
    assert manifest["consistency"] == "unverified" and manifest["source_refs"] == []
    assert t.column("source_device_key").to_pylist() == [None, None]


def test_source_manifest_and_arrow_identity_are_read_only(mini_db, tmp_path):
    import hashlib
    from uuid import uuid4

    origin, batch_id, device_key = (str(uuid4()) for _ in range(3))
    with sqlite3.connect(mini_db) as conn:
        conn.execute("ALTER TABLE batches ADD COLUMN origin_id TEXT")
        conn.execute("ALTER TABLE batches ADD COLUMN source_batch_id TEXT")
        conn.execute("ALTER TABLE devices ADD COLUMN source_device_key TEXT")
        conn.execute("CREATE TABLE sync_revisions(batch_id INTEGER PRIMARY KEY, revision INTEGER)")
        conn.execute("UPDATE batches SET origin_id=?,source_batch_id=?", (origin, batch_id))
        conn.execute("UPDATE devices SET source_device_key=? WHERE id=1", (device_key,))
        conn.execute("UPDATE devices SET source_device_key=? WHERE id=2", (str(uuid4()),))
        conn.execute("INSERT INTO sync_revisions VALUES(1,7)")
    digest = hashlib.sha256(mini_db.read_bytes()).hexdigest()
    out = tmp_path / "new-webdata"
    manifest = export_all(mini_db, None, out)
    assert manifest["consistency"] == "verified"
    assert manifest["source_refs"] == [
        {
            "origin_id": origin,
            "source_batch_id": batch_id,
            "source_device_key": None,
            "source_revision": 7,
        }
    ]
    assert (
        _read_arrow_gz(out / "devices.arrow.gz").column("source_device_key")[0].as_py()
        == device_key
    )
    assert hashlib.sha256(mini_db.read_bytes()).hexdigest() == digest


def test_export_surrogate_webdata(tmp_path):
    sd = tmp_path / "surr"
    sd.mkdir()
    n_freq = 11
    np.savez_compressed(
        sd / "baseline.npz",
        pfs=np.array([0.0, 1.0]),
        areas=np.array([700.0, 1500.0]),
        params_base=np.tile(np.arange(7, dtype=np.float64), (2, 2, 1)),
        z_base_full=np.full((2, 2, n_freq), 40.0),
    )
    (sd / "meta.json").write_text(
        json.dumps(
            {
                "freq_ghz": list(np.linspace(4.0, 7.0, n_freq)),
                "k": 4,
                "x_mean": [0.5] * 5,
                "x_std": [1.0] * 5,
                "params_mean": [0.0] * 7,
                "params_std": [1.0] * 7,
                "z_mean": [42.0],
                "z_std": [3.0],
            }
        )
    )
    np.savez_compressed(
        sd / "dataset.npz",
        x_struct=np.array([[1.0, 0.5, 1.0, 700, 0], [1.0, 0.5, 1.0, 700, 0]], dtype=np.float32),
        full_z=np.stack([np.zeros(n_freq), np.full(n_freq, 2.0)]).astype(np.float32),
    )
    out = tmp_path / "webdata"
    out.mkdir()
    written, missing = export_surrogate_webdata(sd, out)
    assert missing == []
    assert set(written) == {"baseline.json", "surr_meta.json", "cell_means.arrow.gz"}
    t = _read_arrow_gz(out / "cell_means.arrow.gz")
    assert t.num_rows == n_freq  # 1 格 × 11 点
    assert t.column("z").to_pylist()[0] == 1.0  # mean(0, 2)
    bl = json.loads((out / "baseline.json").read_text())
    assert bl["areas"] == [700.0, 1500.0] and len(bl["z_base_full"]) == 2


def test_export_surrogate_webdata_missing(tmp_path):
    sd = tmp_path / "empty"
    sd.mkdir()
    out = tmp_path / "webdata"
    out.mkdir()
    written, missing = export_surrogate_webdata(sd, out)
    assert written == []
    assert set(missing) == {"baseline.json", "surr_meta.json", "cell_means.arrow.gz"}


def test_soft_deleted_batch_excluded_from_static_snapshot(mini_db, tmp_path):
    with sqlite3.connect(mini_db) as conn:
        conn.execute('ALTER TABLE batches ADD COLUMN deleted_at TEXT')
        conn.execute("UPDATE batches SET deleted_at='2026-10-10'")
    out = tmp_path / 'deleted-snapshot'
    export_all(mini_db, surrogate_dir=None, out_dir=out)
    assert _read_arrow_gz(out / 'devices.arrow.gz').num_rows == 0
    assert json.loads((out / 'batches.json').read_text()) == []
    stats = json.loads((out / 'stats.json').read_text())
    assert stats['devices'] == stats['batches'] == 0


def test_comparison_proof_matches_publication_and_rejects_changed_metrics(tmp_path):
    import hashlib

    from sqlalchemy import select

    from app.models import Device
    from tests.fixtures.platform_db import platform_database
    from tests.services.test_comparison_service import add_cohort

    with platform_database("sqlite", tmp_path) as (db, batch):
        add_cohort(db, batch, [10, 20, None, 40])
        db_path = db.bind.url.database
        output = tmp_path / "published"
        manifest = export_all(db_path, None, output)
        proof = manifest["comparison_sources"]
        assert len(proof) == 1
        assert proof[0]["proven_metrics"] == ["qs"]
        assert proof[0]["conditions"]["frequency_ghz"] == [1, 3]
        assert proof[0]["source"]["source_device_key"] is None
        assert manifest["devices_sha256"] == hashlib.sha256(
            (output / "devices.arrow.gz").read_bytes()
        ).hexdigest()
        row = db.scalar(select(Device).where(Device.batch_id == batch.id))
        row.qs = 999
        db.commit()
        changed = export_all(db_path, None, tmp_path / "changed")
        assert changed["comparison_sources"] == []
