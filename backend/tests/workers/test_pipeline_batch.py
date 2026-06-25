"""Tests for pipeline_batch task skeleton and dispatch guard."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.config import get_settings
from app.workers.pipeline_batch import should_use_pipeline


@pytest.fixture
def zip_with_calibration(tmp_path: Path) -> Path:
    """Create a zip containing an OPEN calibration .s2p file."""
    zip_path = tmp_path / "batch_with_cal.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("DUT_001.s2p", "# dummy s2p\n1 2 3 4 5 6 7 8 9\n")
        zf.writestr("OPEN_001.s2p", "# dummy open\n1 2 3 4 5 6 7 8 9\n")
    return zip_path


@pytest.fixture
def zip_without_calibration(tmp_path: Path) -> Path:
    """Create a zip with no calibration files."""
    zip_path = tmp_path / "batch_no_cal.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("DUT_001.s2p", "# dummy s2p\n1 2 3 4 5 6 7 8 9\n")
    return zip_path


class TestShouldUsePipeline:
    """Dispatch guard logic for choosing pipeline vs legacy path."""

    def test_should_use_pipeline_true(self, zip_with_calibration: Path) -> None:
        """deembed=True + zip contains OPEN/SHORT calibration -> True."""
        settings = get_settings()
        assert settings.PIPELINE_ENABLED is True
        result = should_use_pipeline(str(zip_with_calibration), deembed=True)
        assert result is True

    def test_should_use_pipeline_false_when_no_deembed(self, zip_with_calibration: Path) -> None:
        """deembed=False -> False regardless of zip contents."""
        result = should_use_pipeline(str(zip_with_calibration), deembed=False)
        assert result is False

    def test_should_use_pipeline_false_when_disabled(
        self, zip_with_calibration: Path, monkeypatch
    ) -> None:
        """PIPELINE_ENABLED=False -> False regardless of zip contents."""
        settings = get_settings()
        monkeypatch.setattr(settings, "PIPELINE_ENABLED", False)
        result = should_use_pipeline(str(zip_with_calibration), deembed=True)
        assert result is False

    def test_should_use_pipeline_false_when_no_calibration(
        self, zip_without_calibration: Path
    ) -> None:
        """deembed=True but zip has no calibration -> False."""
        result = should_use_pipeline(str(zip_without_calibration), deembed=True)
        assert result is False
