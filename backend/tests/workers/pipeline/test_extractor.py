import zipfile
from pathlib import Path

import pytest

from app.workers.pipeline.extractor import zip_contains_calibration


def test_zip_contains_calibration_true(tmp_path: Path) -> None:
    zip_path = tmp_path / "with_cal.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("DUT_1.s2p", "# dummy\n1 1 1 1 1 1 1 1 1\n")
        zf.writestr("OPEN_1.s2p", "# dummy\n1 1 1 1 1 1 1 1 1\n")
        zf.writestr("SHORT_1.s2p", "# dummy\n1 1 1 1 1 1 1 1 1\n")
    assert zip_contains_calibration(zip_path) is True


def test_zip_contains_calibration_false(tmp_path: Path) -> None:
    zip_path = tmp_path / "no_cal.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("DUT_1.s2p", "# dummy\n1 1 1 1 1 1 1 1 1\n")
    assert zip_contains_calibration(zip_path) is False
