"""Tests for DutProcessor.

TDD: write failing test first, then implement.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import numpy as np

from app.workers.pipeline.processor import DutProcessor


def _write_minimal_s2p_with_resonance(path: Path, n_points: int = 200) -> None:
    """Write a minimal S2P file with synthetic resonance on both ports.

    S2P format: freq + S11_re/im + S21_re/im + S12_re/im + S22_re/im = 9 columns.
    Both S11 and S22 use the same synthetic resonance data.
    S21 and S12 are near-zero transmission.
    """
    freq = np.linspace(1.0e9, 5.0e9, n_points)
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

    s21 = np.zeros_like(s)
    s12 = np.zeros_like(s)

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write("# Hz S RI R 50.0\n")
        for f_hz, s11r, s11i, s21r, s21i, s12r, s12i, s22r, s22i in zip(
            freq,
            s.real,
            s.imag,
            s21.real,
            s21.imag,
            s12.real,
            s12.imag,
            s.real,
            s.imag,
            strict=False,
        ):
            f.write(
                f"{f_hz:.6e} {s11r:.12e} {s11i:.12e} {s21r:.12e} {s21i:.12e} "
                f"{s12r:.12e} {s12i:.12e} {s22r:.12e} {s22i:.12e}\n"
            )


def _write_minimal_s1p_with_resonance(path: Path, n_points: int = 200) -> None:
    """Write a minimal S1P file with a synthetic resonance curve.

    The impedance magnitude follows a pattern that passes resonance detection:
    - high at low freq
    - dips to minimum at resonance (fs)
    - peaks at anti-resonance (fp)
    - then decreases slightly at high freq
    Phase is also realistic so Qs/Qp from phase derivative are non-zero.
    """
    freq = np.linspace(1.0e9, 5.0e9, n_points)
    fs = 2.5e9  # series resonance
    fp = 2.8e9  # parallel resonance

    # Realistic impedance magnitude: high → dip → peak → lower
    z_mag = (
        100.0
        - 80.0 * np.exp(-(((freq - fs) / 0.15e9) ** 2))
        + 120.0 * np.exp(-(((freq - fp) / 0.1e9) ** 2))
    )
    z_mag = np.maximum(z_mag, 1.0)

    # Realistic phase: sharp transition around fs, slower around fp
    # This gives non-zero phase derivative for Q calculation
    phase = np.zeros_like(freq)
    for i, f in enumerate(freq):
        if f < fs:
            phase[i] = -0.5 + 0.3 * (f - 1.0e9) / (fs - 1.0e9)
        elif f < fp:
            phase[i] = -0.5 + 1.5 * (f - fs) / (fp - fs)
        else:
            phase[i] = 1.0 - 0.2 * (f - fp) / (5.0e9 - fp)

    # Convert Z_mag + phase to complex Z, then to S11
    z0 = 50.0
    z_complex = z_mag * np.exp(1j * phase)
    s = (z_complex - z0) / (z_complex + z0)

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write("# Hz S RI R 50.0\n")
        for f_hz, sr, si in zip(freq, s.real, s.imag, strict=False):
            f.write(f"{f_hz:.6e} {sr:.12e} {si:.12e}\n")


class TestDutProcessorS1P:
    def test_s1p_processing_extracts_params_and_archives(self, tmp_path: Path) -> None:
        """S1P item should be processed, params extracted, and raw file gzipped."""
        processor = DutProcessor(compress_raw=True, keep_deembed_temp=False)

        s1p_path = tmp_path / "DUT_1_S11.s1p"
        _write_minimal_s1p_with_resonance(s1p_path)

        item = {"type": "s1p", "path": str(s1p_path)}
        result = processor.process(
            item=item,
            mapping=None,
            wafer=1,
            cal_index=None,
            target_dir=tmp_path,
        )

        assert result["ok"] is True
        assert len(result["rows"]) == 1
        assert len(result["failures"]) == 0
        assert len(result["archived"]) == 1

        row = result["rows"][0]
        assert row["original_filename"] == "DUT_1_S11.s1p"
        assert row["s_param_port"] == "S11"
        assert row["wafer"] == 1
        assert row["deembedded"] is False
        # Key resonator params should be present and physically reasonable
        assert 1.0 < row["fs_ghz"] < row["fp_ghz"] < 5.0
        assert row["zs_ohm"] > 0
        assert row["zp_ohm"] > row["zs_ohm"]
        assert 0 < row["qs"] < 100000
        assert 0 < row["qp"] < 100000
        assert 0 < row["k2eff_pct"] < 50

        # Raw file should be gzipped
        archived = result["archived"][0]
        assert archived.endswith(".s1p.gz")
        assert Path(archived).exists()
        # Verify it's a valid gzip
        with gzip.open(archived, "rt") as f:
            first_line = f.readline()
            assert first_line.startswith("# Hz")

        # Original s1p should be removed after gzip
        assert not s1p_path.exists()

    def test_s1p_no_compress_keeps_raw(self, tmp_path: Path) -> None:
        """When compress_raw=False, original file should remain."""
        processor = DutProcessor(compress_raw=False, keep_deembed_temp=False)

        s1p_path = tmp_path / "DUT_2_S11.s1p"
        _write_minimal_s1p_with_resonance(s1p_path)

        item = {"type": "s1p", "path": str(s1p_path)}
        result = processor.process(
            item=item,
            mapping=None,
            wafer=2,
            cal_index=None,
            target_dir=tmp_path,
        )

        assert result["ok"] is True
        assert len(result["rows"]) == 1
        assert len(result["archived"]) == 0
        assert s1p_path.exists()

    def test_s1p_failure_handled_gracefully(self, tmp_path: Path) -> None:
        """Invalid s1p should return ok=False with failure recorded, no exception."""
        processor = DutProcessor(compress_raw=True, keep_deembed_temp=False)

        bad_s1p = tmp_path / "bad.s1p"
        bad_s1p.write_text("# Hz S RI R 50.0\n1.0e9 0.1 0.2\n")

        item = {"type": "s1p", "path": str(bad_s1p)}
        result = processor.process(
            item=item,
            mapping=None,
            wafer=1,
            cal_index=None,
            target_dir=tmp_path,
        )

        assert result["ok"] is False
        assert len(result["rows"]) == 0
        assert len(result["failures"]) == 1
        assert "bad.s1p" in result["failures"][0]
        # Original should still be archived even on failure? No, only on success
        # Actually, let's keep it simple: archive only on success
        assert len(result["archived"]) == 0


class TestDutProcessorS2P:
    def test_s2p_processing_extracts_params_and_archives(self, tmp_path: Path) -> None:
        """S2P item should be split, params extracted for both ports, and raw file gzipped."""
        processor = DutProcessor(compress_raw=True, keep_deembed_temp=False)

        s2p_path = tmp_path / "DUT_1.s2p"
        _write_minimal_s2p_with_resonance(s2p_path)

        item = {"type": "s2p", "path": str(s2p_path)}
        result = processor.process(
            item=item,
            mapping=None,
            wafer=1,
            cal_index=None,
            target_dir=tmp_path,
        )

        assert result["ok"] is True
        assert len(result["rows"]) == 2
        assert len(result["failures"]) == 0
        assert len(result["archived"]) == 1

        # Both ports should have resonator params
        ports = {row["s_param_port"] for row in result["rows"]}
        assert ports == {"S11", "S22"}
        for row in result["rows"]:
            assert 1.0 < row["fs_ghz"] < row["fp_ghz"] < 5.0
            assert row["zs_ohm"] > 0
            assert row["zp_ohm"] > row["zs_ohm"]
            assert 0 < row["qs"] < 100000
            assert 0 < row["qp"] < 100000
            assert 0 < row["k2eff_pct"] < 50

        # Raw S2P should be gzipped
        archived = result["archived"][0]
        assert archived.endswith(".s2p.gz")
        assert Path(archived).exists()
        with gzip.open(archived, "rt") as f:
            first_line = f.readline()
            assert first_line.startswith("# Hz")

        # Original s2p should be removed after gzip
        assert not s2p_path.exists()

    def test_s2p_split_failure_handled_gracefully(self, tmp_path: Path) -> None:
        """Invalid S2P should return ok=False, failure recorded, and empty dirs cleaned up."""
        processor = DutProcessor(compress_raw=True, keep_deembed_temp=False)

        bad_s2p = tmp_path / "bad.s2p"
        bad_s2p.write_text("# Hz S RI R 50.0\n1.0e9 0.1 0.2 0.3 0.4 0.5 0.6\n")

        item = {"type": "s2p", "path": str(bad_s2p)}
        result = processor.process(
            item=item,
            mapping=None,
            wafer=1,
            cal_index=None,
            target_dir=tmp_path,
        )

        assert result["ok"] is False
        assert len(result["rows"]) == 0
        assert len(result["failures"]) >= 1
        assert "bad.s2p" in result["failures"][0]
        assert len(result["archived"]) == 0

        # Empty S11/S22 dirs should be cleaned up
        assert not (tmp_path / "S11").exists()
        assert not (tmp_path / "S22").exists()

    def test_s2p_no_compress_keeps_raw(self, tmp_path: Path) -> None:
        """When compress_raw=False, original S2P file should remain."""
        processor = DutProcessor(compress_raw=False, keep_deembed_temp=False)

        s2p_path = tmp_path / "DUT_2.s2p"
        _write_minimal_s2p_with_resonance(s2p_path)

        item = {"type": "s2p", "path": str(s2p_path)}
        result = processor.process(
            item=item,
            mapping=None,
            wafer=2,
            cal_index=None,
            target_dir=tmp_path,
        )

        assert result["ok"] is True
        assert len(result["rows"]) == 2
        assert len(result["archived"]) == 0
        assert s2p_path.exists()


class TestDutProcessorEdgeCases:
    def test_missing_path_returns_failure(self, tmp_path: Path) -> None:
        """Item with missing 'path' should record failure, not raise KeyError."""
        processor = DutProcessor(compress_raw=True, keep_deembed_temp=False)

        item = {"type": "s1p"}
        result = processor.process(
            item=item,
            mapping=None,
            wafer=1,
            cal_index=None,
            target_dir=tmp_path,
        )

        assert result["ok"] is False
        assert len(result["rows"]) == 0
        assert len(result["failures"]) == 1
        assert "missing path" in result["failures"][0]
        assert len(result["archived"]) == 0

    def test_unknown_type_returns_failure(self, tmp_path: Path) -> None:
        """Unknown item type should record failure, not raise exception."""
        processor = DutProcessor(compress_raw=True, keep_deembed_temp=False)

        item = {"type": "xyz", "path": str(tmp_path / "foo.txt")}
        result = processor.process(
            item=item,
            mapping=None,
            wafer=1,
            cal_index=None,
            target_dir=tmp_path,
        )

        assert result["ok"] is False
        assert len(result["rows"]) == 0
        assert len(result["failures"]) == 1
        assert "unknown type 'xyz'" in result["failures"][0]
        assert len(result["archived"]) == 0
