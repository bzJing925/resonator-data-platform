"""Tests for CalibrationIndex."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.deembed import DeembedError, DeembedMethod
from app.workers.pipeline.calibration import CalibrationIndex


def _write_minimal_s2p(path: Path) -> None:
    """Write a minimal valid S2P file with 2 data rows (9 columns each)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Hz S RI R 50.0\n"
        "1.0e9 0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8\n"
        "2.0e9 0.2 0.3 0.4 0.5 0.6 0.7 0.8 0.9\n"
    )


class TestCalibrationIndexBuild:
    def test_build_splits_s2p_into_s11_and_s22(self, tmp_path: Path) -> None:
        """Each cal .s2p should be split into cal_S11 and cal_S22 dirs."""
        open_s2p = tmp_path / "OPEN_1.s2p"
        short_s2p = tmp_path / "SHORT_1.s2p"
        _write_minimal_s2p(open_s2p)
        _write_minimal_s2p(short_s2p)

        target_dir = tmp_path / "target"
        index = CalibrationIndex.build(
            target_dir=target_dir,
            cal_s2p_files=[open_s2p, short_s2p],
            method="default",
        )

        assert len(index.s11_paths) == 2
        assert len(index.s22_paths) == 2
        assert all(p.parent.name == "cal_S11" for p in index.s11_paths)
        assert all(p.parent.name == "cal_S22" for p in index.s22_paths)
        assert index.method == DeembedMethod.DEFAULT

    def test_build_empty_raises(self, tmp_path: Path) -> None:
        """No calibration files should raise DeembedError."""
        with pytest.raises(DeembedError):
            CalibrationIndex.build(
                target_dir=tmp_path / "target",
                cal_s2p_files=[],
                method="default",
            )


class TestCalibrationIndexMatch:
    def test_match_s11_port(self, tmp_path: Path) -> None:
        """S11 port DUT should be matched with correct open/short from s11_paths."""
        open_s2p = tmp_path / "OPEN_1.s2p"
        short_s2p = tmp_path / "SHORT_1.s2p"
        _write_minimal_s2p(open_s2p)
        _write_minimal_s2p(short_s2p)

        target_dir = tmp_path / "target"
        index = CalibrationIndex.build(
            target_dir=target_dir,
            cal_s2p_files=[open_s2p, short_s2p],
            method="default",
        )

        dut_s1p = tmp_path / "DUT_1_S11.s1p"
        dut_s1p.write_text("# Hz S RI R 50.0\n1.0e9 0.1 0.2\n")

        open_path, short_path = index.match(port="S11", dut_s1p_path=dut_s1p)

        assert "OPEN" in open_path.name.upper()
        assert "SHORT" in short_path.name.upper()
        assert open_path.parent.name == "cal_S11"
        assert short_path.parent.name == "cal_S11"

    def test_match_s22_port(self, tmp_path: Path) -> None:
        """S22 port DUT should be matched with correct open/short from s22_paths."""
        open_s2p = tmp_path / "OPEN_1.s2p"
        short_s2p = tmp_path / "SHORT_1.s2p"
        _write_minimal_s2p(open_s2p)
        _write_minimal_s2p(short_s2p)

        target_dir = tmp_path / "target"
        index = CalibrationIndex.build(
            target_dir=target_dir,
            cal_s2p_files=[open_s2p, short_s2p],
            method="default",
        )

        dut_s1p = tmp_path / "DUT_1_S22.s1p"
        dut_s1p.write_text("# Hz S RI R 50.0\n1.0e9 0.1 0.2\n")

        open_path, short_path = index.match(port="S22", dut_s1p_path=dut_s1p)

        assert "OPEN" in open_path.name.upper()
        assert "SHORT" in short_path.name.upper()
        assert open_path.parent.name == "cal_S22"
        assert short_path.parent.name == "cal_S22"

    def test_match_no_calibration_raises(self, tmp_path: Path) -> None:
        """If cal_paths are empty, match should raise DeembedError."""
        index = CalibrationIndex(
            s11_paths=[],
            s22_paths=[],
            method=DeembedMethod.DEFAULT,
        )
        dut_s1p = tmp_path / "DUT_1_S11.s1p"
        dut_s1p.write_text("# Hz S RI R 50.0\n1.0e9 0.1 0.2\n")

        with pytest.raises(DeembedError):
            index.match(port="S11", dut_s1p_path=dut_s1p)
