"""Upgrade guard observes unfinished work without mutating the installation."""

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "native_idle", Path(__file__).resolve().parents[1] / "check_native_idle.py"
)
idle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(idle)


@pytest.fixture
def installed(tmp_path):
    root = tmp_path.resolve() / "installed"
    data = tmp_path.resolve() / "data"
    root.mkdir()
    data.mkdir()
    (root / "server-config.json").write_text(json.dumps({"data_dir": str(data)}))
    (root / "native-bundle.json").write_text(json.dumps({"protocol": 2}))
    database = data / "aln-data.db"
    with sqlite3.connect(database) as db:
        for table in ("upload_drafts", "upload_tasks", "export_jobs"):
            db.execute(f"CREATE TABLE {table} (status TEXT NOT NULL)")
    return root, database


@pytest.mark.parametrize("table,status", [
    ("upload_drafts", "preparing"), ("upload_tasks", "pending"),
    ("upload_tasks", "running"), ("export_jobs", "cancel_requested"),
])
def test_unfinished_work_blocks_before_stop_and_preserves_database(installed, table, status):
    root, database = installed
    with sqlite3.connect(database) as db:
        db.execute(f"INSERT INTO {table} VALUES (?)", (status,))
    before = database.read_bytes()
    report = idle.inspect(root)
    assert report["read_only"] and not report["idle"]
    assert report["unfinished"][table] == 1
    assert database.read_bytes() == before


def test_ready_drafts_and_finished_work_allow_upgrade(installed):
    root, database = installed
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO upload_drafts VALUES ('ready'), ('failed'), ('expired')")
        db.execute("INSERT INTO upload_tasks VALUES ('success'), ('failed'), ('cancelled')")
    assert idle.inspect(root)["idle"]


def test_known_old_schema_without_drafts_is_supported(installed):
    root, database = installed
    with sqlite3.connect(database) as db:
        db.execute("DROP TABLE upload_drafts")
        db.execute("DROP TABLE export_jobs")
    assert idle.inspect(root)["idle"]


def test_unreadable_database_fails_closed(installed):
    root, database = installed
    database.unlink()
    with pytest.raises((sqlite3.Error, FileNotFoundError)):
        idle.inspect(root)
    assert not database.exists()
