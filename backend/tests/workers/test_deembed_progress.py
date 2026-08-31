from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from app.core.deembed import _run_deembed


def test_run_deembed_calls_progress(tmp_path: Path) -> None:
    pairs = [(Path(f"dut{i}_S11.s1p"), Path(f"dut{i}_S22.s1p")) for i in range(3)]
    cb = MagicMock()
    with (
        patch("app.core.touchstone.split_s2p_to_s1p") as mock_split,
        patch("app.core.deembed.match_calibration") as mock_match,
        patch("app.core.deembed.deembed"),
    ):
        mock_split.side_effect = lambda p, **kw: MagicMock(s11_path=p, s22_path=p)
        mock_match.return_value = (Path("open.s1p"), Path("short.s1p"))
        _run_deembed(
            pairs,
            {"open": Path("open.s2p")},
            {"short": Path("short.s2p")},
            tmp_path,
            progress_callback=cb,
        )
    assert cb.call_count == 3
    cb.assert_called_with(3, 3)
