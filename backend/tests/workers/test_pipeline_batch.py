"""Tests for pipeline_batch task coordination logic.

需要本地 PostgreSQL（已在 localhost:15432）+ 真 Redis。
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import select, text

from app.db import SessionLocal, engine
from app.models import Batch, Device, Mapping, UploadTask
from app.workers import celery_app
from app.workers.pipeline_batch import pipeline_batch_task, should_use_pipeline


class TestShouldUsePipeline:
    """Dispatch guard tests."""

    def test_should_use_pipeline_true(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "with_cal.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("DUT.s2p", "#\n")
            zf.writestr("OPEN.s2p", "#\n")
        assert should_use_pipeline(zip_path, deembed=True) is True

    def test_should_use_pipeline_false_when_no_deembed(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "with_cal.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("DUT.s2p", "#\n")
            zf.writestr("OPEN.s2p", "#\n")
        assert should_use_pipeline(zip_path, deembed=False) is False

    def test_should_use_pipeline_false_when_no_calibration(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "no_cal.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("DUT.s2p", "#\n")
        assert should_use_pipeline(zip_path, deembed=True) is False


@pytest.fixture(scope="module", autouse=True)
def _eager_mode():
    """让 .delay() 在调用线程内同步执行。"""
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield
    celery_app.conf.task_always_eager = False
    celery_app.conf.task_eager_propagates = False


@pytest.fixture
def clean_db():
    """每个测试跑前清理 devices/batches/upload_tasks/mappings。"""
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE devices, batches, upload_tasks, mapping_entries, mappings "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield


@pytest.fixture
def sample_s2p_content():
    """Minimal valid Touchstone S2P content with synthetic resonance.

    Generates enough points (>= 10) and a realistic resonance curve so that
    extract_resonator_params can successfully compute fs/fp/qs/qp/k2eff.
    """
    freq = np.linspace(1.0e9, 5.0e9, 200)
    fs = 2.5e9
    fp = 2.8e9

    z_mag = (
        100.0
        - 80.0 * np.exp(-(((freq - fs) / 0.15e9) ** 2))
        + 120.0 * np.exp(-(((freq - fp) / 0.1e9) ** 2))
    )
    z_mag = np.maximum(z_mag, 1.0)

    phase = np.zeros_like(freq)
    for i, f in enumerate(freq):
        if f < fs:
            phase[i] = -0.5 + 0.3 * (f - 1.0e9) / (fs - 1.0e9)
        elif f < fp:
            phase[i] = -0.5 + 1.5 * (f - fs) / (fp - fs)
        else:
            phase[i] = 1.0 - 0.2 * (f - fp) / (5.0e9 - fp)

    z0 = 50.0
    z_complex = z_mag * np.exp(1j * phase)
    s = (z_complex - z0) / (z_complex + z0)

    lines = ["# Hz S RI R 50.0\n"]
    for f_hz, s11r, s11i in zip(freq, s.real, s.imag, strict=False):
        # S21/S12 near-zero transmission; S22 same as S11
        lines.append(
            f"{f_hz:.6e} {s11r:.12e} {s11i:.12e} 0.0 0.0 0.0 0.0 {s11r:.12e} {s11i:.12e}\n"
        )
    return "".join(lines)


@pytest.fixture
def pipeline_zip(tmp_path: Path, sample_s2p_content: str) -> Path:
    """Create a zip with 2 DUT s2p (no calibration files).

    This exercises the non-deembed pipeline path using synthetic resonance
    data that is valid enough for extract_resonator_params. The de-embed
    path is covered separately by TestPipelineBatchWithRealZip using #2.zip.
    """
    zip_path = tmp_path / "pipeline_batch.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("DUT_17_E6-1_X0Y0N18.s2p", sample_s2p_content)
        zf.writestr("DUT_18_E6-2_X1Y1N19.s2p", sample_s2p_content)
    return zip_path


@pytest.fixture
def staged_pipeline_files(tmp_path: Path, pipeline_zip: Path, monkeypatch):
    """把 fixtures zip 复制到隔离的 DATA_ROOT，并 monkeypatch settings。"""
    data_root = tmp_path / "aln-data"
    (data_root / "uploads").mkdir(parents=True)
    (data_root / "files").mkdir(parents=True)
    (data_root / "mappings").mkdir(parents=True)

    staged_zip = data_root / "uploads" / "pipeline_batch.zip"
    shutil.copy(pipeline_zip, staged_zip)

    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "DATA_ROOT", data_root, raising=False)
    return staged_zip


@pytest.fixture
def mapping_file(tmp_path: Path) -> Path:
    """Create a minimal valid mapping xlsx for testing."""
    import pandas as pd

    mapping_path = tmp_path / "test_mapping.xlsx"
    df = pd.DataFrame(
        {
            0: ["E6-1", "E6-2"],
            1: ["EG0 FL0 700&5500", "EG0 FL0.5 1200&4500"],
        }
    )
    df.to_excel(mapping_path, index=False, header=False)
    return mapping_path


class TestPipelineBatchTask:
    """End-to-end tests for pipeline_batch_task coordination logic."""

    def test_pipeline_batch_full_pipeline(self, clean_db, staged_pipeline_files, mapping_file):
        """完整跑 pipeline：插 mapping → 插 pending batch + upload_task →
        触发 pipeline_batch_task → 校验入库 device_count == 2 (4 ports)。"""
        staged_zip = staged_pipeline_files
        batch_no = "PIPELINE.01"

        db = SessionLocal()
        try:
            # 准备 mapping 行（pipeline 需要 mapping 存在）
            mapping_row = Mapping(
                name="pipeline_test_mapping",
                file_path=str(mapping_file),
                entry_count=2,
            )
            db.add(mapping_row)
            db.flush()
            mapping_id = mapping_row.id

            # 预占 batch
            batch_row = Batch(
                batch_no=batch_no,
                mapping_id=mapping_id,
                f_start_ghz=None,
                f_end_ghz=None,
                deembedded=True,
                process_type="AUTO",
                file_path="(pending)",
                device_count=0,
                uploaded_by="test",
            )
            db.add(batch_row)

            # 预占 upload_task
            task_row = UploadTask(batch_no=batch_no, status="pending", progress_pct=0)
            db.add(task_row)
            db.flush()
            upload_task_id = task_row.id

            db.commit()
        finally:
            db.close()

        # 触发 pipeline_batch_task（EAGER → 同步执行）
        result = pipeline_batch_task.apply(
            kwargs=dict(
                upload_task_id=upload_task_id,
                zip_path=str(staged_zip),
                batch_no=batch_no,
                mapping_id=mapping_id,
                f_start_ghz=None,
                f_end_ghz=None,
                deembed_method="default",
                process_type="AUTO",
            )
        ).get()

        # 每个 DUT s2p 拆成 2 个 port → 2 DUT × 2 ports = 4 devices
        assert result["device_count"] == 4, (
            f"应入库 4 行（2 DUT × 2 ports），实际 {result['device_count']}"
        )

        # 校验 DB 状态
        db = SessionLocal()
        try:
            batches = db.scalars(select(Batch)).all()
            assert len(batches) == 1
            assert batches[0].batch_no == batch_no
            assert batches[0].device_count == 4

            from sqlalchemy import func

            n = db.scalar(select(func.count(Device.id)).where(Device.batch_id == batches[0].id))
            assert n == 4

            # upload_tasks 最终态
            task = db.get(UploadTask, upload_task_id)
            assert task is not None
            assert task.status == "success"
            assert task.progress_pct == 100
            assert task.finished_at is not None
        finally:
            db.close()


class TestPipelineBatchWithRealZip:
    """用上级目录 #2.zip 做集成验证（可选，文件不存在时自动跳过）。"""

    ZIP_PATH = Path("/Users/jingbozuo/Projects/#2.zip")

    @pytest.mark.integration
    @pytest.mark.skipif(not ZIP_PATH.exists(), reason="#2.zip 不存在")
    def test_2zip_subset_succeeds(self, clean_db, mapping_file, tmp_path: Path, monkeypatch):
        """从 #2.zip 取 2 个 DUT + 对应 OPEN/SHORT 跑通 pipeline。"""
        import shutil
        import subprocess

        subset_dir = tmp_path / "subset"
        subset_dir.mkdir()
        files = [
            "2_A1-1_X0Y0N20_Fail.s2p",
            "2_A1-1_X0Y1N14_Fail.s2p",
            "2_OPEN-1_X0Y0N20_Fail.s2p",
            "2_OPEN-1_X0Y1N14_Fail.s2p",
            "2_SHORT-1_X0Y0N20_Fail.s2p",
            "2_SHORT-1_X0Y1N14_Fail.s2p",
        ]
        subprocess.run(
            ["7z", "x", str(self.ZIP_PATH), f"-o{subset_dir}"] + files,
            check=True,
            capture_output=True,
        )
        subset_zip = tmp_path / "subset.zip"
        subprocess.run(
            ["zip", "-j", str(subset_zip)] + [str(subset_dir / f) for f in files],
            check=True,
            capture_output=True,
        )

        data_root = tmp_path / "aln-data"
        (data_root / "uploads").mkdir(parents=True)
        (data_root / "files").mkdir(parents=True)
        (data_root / "mappings").mkdir(parents=True)
        staged_zip = data_root / "uploads" / "subset.zip"
        shutil.copy(subset_zip, staged_zip)

        from app.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "DATA_ROOT", data_root, raising=False)

        batch_no = "PIPELINE.2ZIP.SUBSET"
        db = SessionLocal()
        try:
            mapping_row = Mapping(
                name="2zip_subset_mapping",
                file_path=str(mapping_file),
                entry_count=2,
            )
            db.add(mapping_row)
            db.flush()
            mapping_id = mapping_row.id

            batch_row = Batch(
                batch_no=batch_no,
                mapping_id=mapping_id,
                deembedded=True,
                file_path="(pending)",
                raw_zip_path=str(staged_zip),
                device_count=0,
                uploaded_by="test",
            )
            db.add(batch_row)
            task_row = UploadTask(batch_no=batch_no, status="pending", progress_pct=0)
            db.add(task_row)
            db.flush()
            upload_task_id = task_row.id
            db.commit()
        finally:
            db.close()

        result = pipeline_batch_task.apply(
            kwargs=dict(
                upload_task_id=upload_task_id,
                zip_path=str(staged_zip),
                batch_no=batch_no,
                mapping_id=mapping_id,
                deembed_method="default",
            )
        ).get(timeout=300)

        assert result["device_count"] == 4, f"期望 4 devices，实际 {result}"
        assert result["failures"] == 0

        db = SessionLocal()
        try:
            task = db.get(UploadTask, upload_task_id)
            assert task is not None
            assert task.status == "success"
        finally:
            db.close()
