"""upload_service 单元测试。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app.models import Batch, Mapping
from app.services.upload_service import (
    _generate_unique_batch_no,
    _split_batch_no,
    create_batch_and_dispatch,
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
