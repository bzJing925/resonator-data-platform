"""De-embedding 在 worker 中的端到端路径测试。

样例 zip `T8901P.01.zip` 不含 OPEN/SHORT 校准件，因此本测试动态构造一个
包含 1 个 DUT + OPEN + SHORT 的临时 ZIP，验证：

1. deembed_enabled=True 且校准件齐 → 实际跑 ShortOpen，写出 *_de.s1p
2. deembed_enabled=True 但缺校准件 → 整个任务标 failed 并报错（不静默跳过）
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import numpy as np
import pytest
import skrf as rf
from sqlalchemy import select, text

from app.db import SessionLocal, engine
from app.models import Batch, Mapping, UploadTask
from app.workers import celery_app
from app.workers.process_batch import process_batch_task


@pytest.fixture(scope="module", autouse=True)
def _eager_mode():
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield
    celery_app.conf.task_always_eager = False
    celery_app.conf.task_eager_propagates = False


@pytest.fixture
def clean_db():
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE devices, batches, upload_tasks, mapping_entries, mappings "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield


def _make_s2p(path: Path, npoints: int = 201, s_diag: complex = 0.5 + 0.0j) -> None:
    """造一个简单 2 端口 s2p（覆盖谐振频段 14–16 GHz，避开 extract 算法的极端路径）。"""
    freq = rf.Frequency(start=14.0, stop=16.0, npoints=npoints, unit="GHz")
    s = np.zeros((npoints, 2, 2), dtype=complex)
    s[:, 0, 0] = s_diag
    s[:, 1, 1] = s_diag
    n = rf.Network(frequency=freq, s=s, z0=50.0)
    path.parent.mkdir(parents=True, exist_ok=True)
    n.write_touchstone(str(path).replace(".s2p", ""))


def _stage(
    tmp_path: Path,
    sample_mapping: Path,
    monkeypatch,
    *,
    include_calibration: bool,
) -> tuple[Path, Path]:
    """生成一个含 1 个 DUT（+ 可选 OPEN/SHORT）的 zip，返回 (zip_path, mapping_path)。"""
    data_root = tmp_path / "aln-data"
    (data_root / "uploads").mkdir(parents=True)
    (data_root / "files").mkdir(parents=True)
    (data_root / "mappings").mkdir(parents=True)

    # 拷贝 mapping
    staged_mapping = data_root / "mappings" / "mapping_ELB003.xlsx"
    shutil.copy(sample_mapping, staged_mapping)

    # 造 zip 内容
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    # DUT: 用 mapping 里存在的 mark + 坐标，避免 extract 因找不到面积失败
    _make_s2p(src_dir / "17_E6-1_X0Y0N18_Fail.s2p")
    if include_calibration:
        _make_s2p(src_dir / "OPEN.s2p", s_diag=0.99 + 0.0j)
        _make_s2p(src_dir / "SHORT.s2p", s_diag=-0.99 + 0.0j)

    staged_zip = data_root / "uploads" / "T_DEEMBED_TEST.01.zip"
    with zipfile.ZipFile(staged_zip, "w") as zf:
        for p in src_dir.glob("*.s2p"):
            zf.write(p, arcname=p.name)

    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "DATA_ROOT", data_root, raising=False)
    return staged_zip, staged_mapping


def _seed_pending_rows(batch_no: str, mapping_path: Path) -> tuple[int, int]:
    db = SessionLocal()
    try:
        m = Mapping(name="m_de", file_path=str(mapping_path), entry_count=0)
        db.add(m)
        db.flush()

        b = Batch(
            batch_no=batch_no,
            mapping_id=m.id,
            f_start_ghz=None,
            f_end_ghz=None,
            deembedded=True,
            process_type="S1P",
            file_path="(pending)",
            device_count=0,
            uploaded_by="test",
        )
        db.add(b)
        t = UploadTask(batch_no=batch_no, status="pending", progress_pct=0)
        db.add(t)
        db.flush()
        ids = (m.id, t.id)
        db.commit()
        return ids
    finally:
        db.close()


def test_deembed_with_calibration_writes_de_s1p(clean_db, tmp_path, sample_mapping, monkeypatch):
    """OPEN/SHORT 齐全时，worker 应当真的去嵌并把 *_de.s1p 喂给 extract。"""
    staged_zip, staged_mapping = _stage(
        tmp_path, sample_mapping, monkeypatch, include_calibration=True
    )
    batch_no = "T_DEEMBED_TEST.01"
    mapping_id, task_id = _seed_pending_rows(batch_no, staged_mapping)

    # 触发任务（EAGER），即使 extract 个别失败也不抛——我们只看去嵌产物
    process_batch_task.apply(
        kwargs=dict(
            upload_task_id=task_id,
            zip_path=str(staged_zip),
            batch_no=batch_no,
            mapping_id=mapping_id,
            f_start_ghz=None,
            f_end_ghz=None,
            deembed_enabled=True,
            process_type="S1P",
        )
    ).get()

    # 期望 files_dir/<batch>/S11_de 和 S22_de 各有 1 个 *_de.s1p
    from app.config import get_settings

    files_dir = get_settings().files_dir / batch_no
    s11_de = list((files_dir / "S11_de").glob("*_de.s1p"))
    s22_de = list((files_dir / "S22_de").glob("*_de.s1p"))
    assert len(s11_de) == 1, f"S11_de 应有 1 个文件，实际 {[p.name for p in s11_de]}"
    assert len(s22_de) == 1, f"S22_de 应有 1 个文件，实际 {[p.name for p in s22_de]}"

    # batches.deembedded = True
    db = SessionLocal()
    try:
        batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
        assert batch is not None
        assert batch.deembedded is True
    finally:
        db.close()


def test_deembed_without_calibration_fails_loudly(clean_db, tmp_path, sample_mapping, monkeypatch):
    """缺 OPEN/SHORT 但用户开了 deembed → 任务必须失败并落 upload_tasks=failed。"""
    staged_zip, staged_mapping = _stage(
        tmp_path, sample_mapping, monkeypatch, include_calibration=False
    )
    batch_no = "T_DEEMBED_NOCAL.01"
    mapping_id, task_id = _seed_pending_rows(batch_no, staged_mapping)

    with pytest.raises(Exception) as exc_info:
        process_batch_task.apply(
            kwargs=dict(
                upload_task_id=task_id,
                zip_path=str(staged_zip),
                batch_no=batch_no,
                mapping_id=mapping_id,
                f_start_ghz=None,
                f_end_ghz=None,
                deembed_enabled=True,
                process_type="S1P",
            )
        ).get()

    assert "OPEN/SHORT" in str(exc_info.value)

    db = SessionLocal()
    try:
        task = db.get(UploadTask, task_id)
        assert task is not None
        assert task.status == "failed"
        assert task.error_msg is not None and "OPEN/SHORT" in task.error_msg
    finally:
        db.close()
