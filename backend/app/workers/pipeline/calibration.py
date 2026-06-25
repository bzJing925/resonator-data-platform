"""CalibrationIndex for de-embedding lookup.

Builds an index by splitting calibration .s2p files into S11/S22 .s1p,
then matches DUT ports to the correct open/short calibration files.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.deembed import DeembedError, DeembedMethod, match_calibration
from app.core.touchstone import split_s2p_to_s1p


@dataclass
class CalibrationIndex:
    """Index of split calibration S1P files for S11 and S22 ports."""

    s11_paths: list[Path]
    s22_paths: list[Path]
    method: DeembedMethod

    @classmethod
    def build(
        cls,
        target_dir: Path,
        cal_s2p_files: list[Path],
        method: DeembedMethod,
    ) -> CalibrationIndex:
        """Split each calibration .s2p into S11/S22 and store under target_dir.

        Args:
            target_dir: Directory where cal_S11/ and cal_S22/ subdirs are created.
            cal_s2p_files: List of calibration .s2p file paths.
            method: De-embedding method for matching.

        Returns:
            A CalibrationIndex with populated s11_paths and s22_paths.

        Raises:
            DeembedError: If cal_s2p_files is empty.
        """
        if not cal_s2p_files:
            raise DeembedError("No calibration .s2p files provided")

        cal_s11_dir = target_dir / "cal_S11"
        cal_s22_dir = target_dir / "cal_S22"

        s11_paths: list[Path] = []
        s22_paths: list[Path] = []

        for s2p_path in cal_s2p_files:
            result = split_s2p_to_s1p(
                s2p_path,
                out_dir_s11=cal_s11_dir,
                out_dir_s22=cal_s22_dir,
            )
            s11_paths.append(result.s11_path)
            s22_paths.append(result.s22_path)

        return cls(s11_paths=s11_paths, s22_paths=s22_paths, method=method)

    def match(self, port: int, dut_s1p_path: Path) -> tuple[Path, Path]:
        """Match a DUT S1P file to open/short calibration files for the given port.

        Args:
            port: 1 for S11, 2 for S22.
            dut_s1p_path: Path to the DUT .s1p file.

        Returns:
            (open_path, short_path) for the specified port.

        Raises:
            DeembedError: If no matching calibration files are found.
        """
        cal_paths = self.s11_paths if port == 1 else self.s22_paths
        return match_calibration(dut_s1p_path, cal_paths, self.method)
