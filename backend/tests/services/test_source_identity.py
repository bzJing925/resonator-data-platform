"""Exact original-member registration and persistent source provenance."""

import pytest
from sqlalchemy import select

from app.models import Batch, Device, SourceInput
from app.services.source_identity import (
    SourceIdentityError,
    backfill_source_refs,
    ensure_origin_identity,
    register_source_input,
    resolve_device_identity,
    update_source_input_path,
    validate_relpath,
)
from tests.fixtures.platform_db import DATABASES, platform_database


@pytest.mark.parametrize("kind", DATABASES)
def test_exact_members_ports_and_path_publication(kind, tmp_path):
    with platform_database(kind, tmp_path) as (db, batch):
        entries = [
            register_source_input(db, batch.id, member, port, "digest", path, "same.s2p")
            for member, port, path in [
                ("a/same.s2p", "S11", "a/same_S11.s1p"),
                ("a/same.s2p", "S22", "a/same_S22.s1p"),
                ("b/same.s2p", "S11", "b/same_S11.s1p"),
                ("A/same.s2p", "S11", "A/same_S11.s1p"),
            ]
        ]
        keys = [entry.source_device_key for entry in entries]
        assert len(set(keys)) == 4
        db.commit()
        origin = ensure_origin_identity(db)
        assert origin == batch.origin_id == ensure_origin_identity(db)
        assert (
            register_source_input(
                db, batch.id, "a/same.s2p", "S11", "digest", "a/same_S11.s1p", "same.s2p"
            ).source_device_key
            == keys[0]
        )
        with pytest.raises(SourceIdentityError, match="内容"):
            register_source_input(db, batch.id, "a/same.s2p", "S11", "changed", None, "same.s2p")
        with pytest.raises(SourceIdentityError, match="精确"):
            resolve_device_identity(db, batch.id, "same.s2p", "S11")
        update_source_input_path(db, keys[0], "new/same_S11.s1p")
        db.commit()
        db.expire_all()
        assert entries[0].current_relpath == "new/same_S11.s1p"
        assert entries[0].member_key == "a/same.s2p"
        assert resolve_device_identity(db, batch.id, "a/same.s2p", "S11") == keys[0]


@pytest.mark.parametrize("kind", DATABASES)
def test_backfill_persists_and_never_invents_remote_provenance(kind, tmp_path):
    with platform_database(kind, tmp_path) as (db, batch):
        device = Device(batch_id=batch.id, original_filename="legacy.s1p")
        remote = Batch(
            batch_no="remote",
            mapping_id=batch.mapping_id,
            file_path="server://remote",
            source="server",
        )
        db.add_all([device, remote])
        db.commit()
        assert backfill_source_refs(db, chunk_size=1) == {
            "batches": 1,
            "devices": 1,
            "unverified_batches": 1,
        }
        db.commit()
        identity = (batch.origin_id, batch.source_batch_id, device.source_device_key)
        assert backfill_source_refs(db, chunk_size=1)["devices"] == 0
        db.commit()
        db.expire_all()
        assert identity == (batch.origin_id, batch.source_batch_id, device.source_device_key)
        assert remote.origin_id is None and remote.source_batch_id is None
        assert db.scalars(select(SourceInput)).all() == []


@pytest.mark.parametrize("path", ["../a", "/a", "a/../b", "a\\b", "C:/a", "a//b", "./a"])
def test_unsafe_original_paths_rejected(path):
    with pytest.raises(SourceIdentityError):
        validate_relpath(path)


@pytest.mark.parametrize("kind", DATABASES)
def test_compute_registered_identity_and_legacy_identity_preserved(kind, tmp_path):
    from app.services.compute_service import _upsert_device, compute_single_device
    from tests.fixtures.platform_cases import _algorithm_samples

    samples = _algorithm_samples(tmp_path)
    path = tmp_path / "dut.s1p"
    path.write_bytes(samples["dut_s1p"])
    with platform_database(kind, tmp_path) as (db, batch):
        batch.mapping.file_path = str(tmp_path / "absent.xlsx")
        # No mapping file: test actual extraction, with the existing empty-map fallback.
        from unittest.mock import patch

        entry = register_source_input(
            db, batch.id, "original/dut.s1p", "S11", "digest", "dut.s1p", "dut.s1p"
        )
        db.commit()
        with patch("app.services.compute_service.load_mapping", return_value={}):
            device = compute_single_device(db, batch, "dut.s1p", path)
            before = (device.id, device.source_device_key)
            device = compute_single_device(db, batch, "dut.s1p", path)
        assert before == (device.id, device.source_device_key)
        assert device.source_device_key == entry.source_device_key
        assert device.fs_ghz is not None
        legacy = _upsert_device(
            db,
            batch,
            "legacy.s1p",
            {
                "batch_id": batch.id,
                "original_filename": "legacy.s1p",
                "s_param_path": "legacy.s1p",
                "s_param_port": "S11",
                "deembedded": False,
            },
        )
        assert legacy.source_device_key is None  # No basename-based provenance invention.
        backfill_source_refs(db)
        db.commit()
        key = legacy.source_device_key
        _upsert_device(
            db,
            batch,
            "legacy.s1p",
            {
                "source_device_key": None,
                "fs_ghz": 2.0,
            },
        )
        assert legacy.source_device_key == key
        register_source_input(db, batch.id, "other/dut.s1p", "S11", "digest", "dut.s1p", "dut.s1p")
        db.commit()
        with pytest.raises(SourceIdentityError, match="多个"):
            _upsert_device(db, batch, "dut.s1p", {"s_param_port": "S11"})


@pytest.mark.parametrize("kind", DATABASES)
def test_virtual_move_and_rename_preserve_readable_actual_path(kind, tmp_path, monkeypatch):
    from app.api import files
    from app.models import FileNode
    from app.schemas.file import FileTreeMoveRequest, FileTreeRenameRequest
    from app.services.data_revision import read_epoch

    with platform_database(kind, tmp_path) as (db, batch):
        base = tmp_path / "active"
        actual = base / "a/dut.s1p"
        actual.parent.mkdir(parents=True)
        actual.write_text("original data")
        monkeypatch.setattr(files, "batch_files_dir", lambda _: base)
        entry = register_source_input(
            db, batch.id, "raw/a/dut.s1p", "S11", "digest", "a/dut.s1p", "dut.s1p"
        )
        device = Device(
            batch_id=batch.id,
            original_filename="dut.s1p",
            s_param_path="a/dut.s1p",
            source_device_key=entry.source_device_key,
        )
        root = FileNode(batch_id=batch.id, node_type="root", name="root")
        db.add_all([device, root])
        db.flush()
        target = FileNode(batch_id=batch.id, parent_id=root.id, node_type="folder", name="b")
        node = FileNode(
            batch_id=batch.id,
            parent_id=root.id,
            node_type="file",
            name="dut.s1p",
            relpath="a/dut.s1p",
        )
        db.add_all([target, node])
        db.commit()
        epoch = read_epoch(db)
        original = (entry.member_key, entry.source_device_key, entry.current_relpath)
        files.move_file_tree_nodes(
            db, FileTreeMoveRequest(node_ids=[node.id], target_folder_id=target.id)
        )
        files.rename_file_tree_node(db, FileTreeRenameRequest(node_id=node.id, name="display.s1p"))
        db.expire_all()
        assert node.parent_id == target.id and node.name == "display.s1p"
        assert (entry.member_key, entry.source_device_key, entry.current_relpath) == original
        assert device.s_param_path == node.relpath == entry.current_relpath == "a/dut.s1p"
        assert actual.read_text() == "original data"
        assert read_epoch(db) == epoch


@pytest.mark.parametrize("kind", DATABASES)
def test_snapshot_roundtrip_preserves_source_uuid_and_revision(kind, tmp_path):
    from app.services.snapshot_service import build_snapshot, decode_devices, import_snapshot
    from app.services.source_identity import batch_source_ref

    with platform_database(kind, tmp_path) as (source, batch):
        source.add(Device(batch_id=batch.id, original_filename="dut.s1p"))
        source.commit()
        backfill_source_refs(source)
        source.commit()
        snapshot = build_snapshot(source, set())
        expected = batch_source_ref(source, batch)
        rows = decode_devices(snapshot["devices_arrow_gz_b64"])
        assert snapshot["manifest"]["consistency"] == "verified"
        assert snapshot["manifest"]["source_refs"] == [expected]
        source.rollback()
        with platform_database(kind, tmp_path) as (replica, existing):
            existing.batch_no = "unrelated-local"
            replica.commit()
            import_snapshot(
                replica, snapshot["batches"], snapshot["mappings"], rows, source="server"
            )
            imported = replica.scalar(select(Batch).where(Batch.batch_no == batch.batch_no))
            device = replica.scalar(select(Device).where(Device.batch_id == imported.id))
            assert batch_source_ref(replica, imported) == expected
            assert device.source_device_key == rows[0]["source_device_key"]
            backfill_source_refs(replica)
            replica.commit()
            assert device.source_device_key == rows[0]["source_device_key"]
