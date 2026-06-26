import shutil
import zipfile
from pathlib import Path

import pytest

from app.workers.pipeline.extractor import StreamingExtractor, zip_contains_calibration


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


def test_zip_contains_calibration_not_a_zip(tmp_path: Path) -> None:
    bad_path = tmp_path / "not_a_zip.txt"
    bad_path.write_text("this is not a zip")
    assert zip_contains_calibration(bad_path) is False


def test_zip_contains_calibration_corrupted_zip(tmp_path: Path) -> None:
    bad_path = tmp_path / "corrupted.zip"
    bad_path.write_bytes(b"PK\x03\x04" + b"trailing garbage")
    assert zip_contains_calibration(bad_path) is False


def test_zip_contains_calibration_case_insensitive(tmp_path: Path) -> None:
    zip_path = tmp_path / "case.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("open_1.s2p", "# dummy\n1 1 1 1 1 1 1 1 1\n")
        zf.writestr("Short_2.S2P", "# dummy\n1 1 1 1 1 1 1 1 1\n")
    assert zip_contains_calibration(zip_path) is True


def test_zip_contains_calibration_substring_no_match(tmp_path: Path) -> None:
    zip_path = tmp_path / "no_cal.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("OPENING.s2p", "# dummy\n1 1 1 1 1 1 1 1 1\n")
        zf.writestr("SHORTED_1.s2p", "# dummy\n1 1 1 1 1 1 1 1 1\n")
    assert zip_contains_calibration(zip_path) is False


def test_zip_contains_calibration_basic_method(tmp_path: Path) -> None:
    zip_path = tmp_path / "basic.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("WO_1.s2p", "# dummy\n1 1 1 1 1 1 1 1 1\n")
        zf.writestr("WS_2.s2p", "# dummy\n1 1 1 1 1 1 1 1 1\n")
    assert zip_contains_calibration(zip_path, method="basic") is True


def test_zip_contains_calibration_basic_no_match(tmp_path: Path) -> None:
    zip_path = tmp_path / "basic_no.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("DUT_1.s2p", "# dummy\n1 1 1 1 1 1 1 1 1\n")
    assert zip_contains_calibration(zip_path, method="basic") is False


def test_zip_contains_calibration_invalid_method() -> None:
    with pytest.raises(ValueError, match="method must be 'default' or 'basic'"):
        zip_contains_calibration("dummy.zip", method="invalid")


def test_streaming_extractor_yields_files(tmp_path: Path) -> None:
    zip_path = tmp_path / "test.zip"
    target_dir = tmp_path / "out"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("a.s1p", "# dummy\n1 1 1\n")
        zf.writestr("b.s1p", "# dummy\n2 2 2\n")

    extractor = StreamingExtractor(zip_path, target_dir)
    found = sorted(p.name for p in extractor.extract())
    assert found == ["a.s1p", "b.s1p"]
    assert (target_dir / "a.s1p").exists()


@pytest.mark.skipif(not shutil.which("7z") and not shutil.which("7za"), reason="无 7z")
def test_streaming_extractor_uses_7z(tmp_path: Path) -> None:
    zip_path = tmp_path / "test.zip"
    target_dir = tmp_path / "out"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("x.s1p", "# dummy\n1 1 1\n")
    extractor = StreamingExtractor(zip_path, target_dir)
    assert Path(extractor.exe).name in ("7z", "7za", "p7zip")
