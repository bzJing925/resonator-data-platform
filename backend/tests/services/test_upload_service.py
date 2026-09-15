"""upload_service 单元测试。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app.models import Batch, Device, Mapping
from app.services.upload_service import (
    _generate_unique_batch_no,
    _split_batch_no,
    create_batch_and_dispatch,
    create_table_batch,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _make_mapping(db: Session, name: str = "test-mapping") -> Mapping:
    mapping = Mapping(name=name, file_path="/dev/null")
    db.add(mapping)
    db.commit()
    db.refresh(mapping)
    return mapping


def _make_batch(db: Session, batch_no: str, mapping_id: int) -> Batch:
    batch = Batch(
        batch_no=batch_no,
        mapping_id=mapping_id,
        file_path="/dev/null",
        raw_zip_path="/dev/null",
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return batch


@pytest.mark.parametrize(
    ("batch_no", "expected"),
    [
        ("#17", ("#17", 0)),
        ("#17-1", ("#17", 1)),
        ("#17-abc", ("#17-abc", 0)),
        ("T8901P.01", ("T8901P.01", 0)),
        ("T8901P.01-1", ("T8901P.01", 1)),
        ("abc-123", ("abc", 123)),
        ("123", ("123", 0)),
        ("", ("", 0)),
    ],
)
def test_split_batch_no(batch_no: str, expected: tuple[str, int]) -> None:
    assert _split_batch_no(batch_no) == expected


def test_generate_unique_batch_no_empty_db(db: Session) -> None:
    assert _generate_unique_batch_no(db, "#17") == "#17"


def test_generate_unique_batch_no_simple_conflict(db: Session) -> None:
    mapping = _make_mapping(db)
    _make_batch(db, "#17", mapping.id)

    assert _generate_unique_batch_no(db, "#17") == "#17-1"


def test_generate_unique_batch_no_continuous_conflict(db: Session) -> None:
    mapping = _make_mapping(db)
    _make_batch(db, "#17", mapping.id)
    _make_batch(db, "#17-1", mapping.id)

    assert _generate_unique_batch_no(db, "#17") == "#17-2"


def test_generate_unique_batch_no_conflict_from_prefixed_name(db: Session) -> None:
    mapping = _make_mapping(db)
    _make_batch(db, "#17-1", mapping.id)

    assert _generate_unique_batch_no(db, "#17-1") == "#17-2"


def test_create_batch_and_dispatch_renames_duplicates(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mapping = _make_mapping(db)
    zip_path = tmp_path / "#17.zip"
    zip_path.write_text("fake zip")

    monkeypatch.setattr(
        "app.services.upload_service.dispatch_batch_task",
        lambda **kwargs: "fake-celery-id",
    )

    first = create_batch_and_dispatch(db, zip_path, "#17", mapping.id)
    second = create_batch_and_dispatch(db, zip_path, "#17", mapping.id)
    third = create_batch_and_dispatch(db, zip_path, "#17", mapping.id)

    assert first is not None
    assert first.batch_no == "#17"
    assert second is not None
    assert second.batch_no == "#17-1"
    assert third is not None
    assert third.batch_no == "#17-2"

    batch_nos = {b.batch_no for b in db.query(Batch).all()}
    assert batch_nos == {"#17", "#17-1", "#17-2"}


def test_create_table_batch_imports_rows_without_worker(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mapping = _make_mapping(db)
    table_path = tmp_path / "parameters.csv"
    table_path.write_text("placeholder")
    rows = [{
        "original_filename": "T_A1-1_X-1Y-1.s1p",
        "display_name": "EG1 FL0.5",
        "mark": "A1-1",
        "coord": "X-1Y-1",
        "x": -1,
        "y": -1,
        "eg": 1.0,
        "fl": 0.5,
        "ag": 0.1,
        "pf": "true",
        "area_um2": 700,
        "fs_ghz": 4.8,
        "fp_ghz": 5.0,
        "zs_ohm": 1.0,
        "zp_ohm": 200.0,
        "qs": 100.0,
        "qp": 120.0,
        "qs_bodeq": 110.0,
        "qp_bodeq": 130.0,
        "k2eff_pct": 5.0,
    }]
    monkeypatch.setattr(
        "app.share_pack.rows_from_xlsx",
        lambda *_args, **_kwargs: (rows, [], {"process_type": "S1P"}, [], {}),
    )

    task = create_table_batch(db, table_path, "#table", mapping.id, deembedded=True)

    assert task.status == "success"
    batch = db.query(Batch).filter_by(batch_no="#table").one()
    assert batch.source == "table" and batch.device_count == 1 and batch.raw_zip_path is None
    device = db.query(Device).filter_by(batch_id=batch.id).one()
    assert device.pf == "Y" and device.s_param_port == "S11" and device.deembedded is True
