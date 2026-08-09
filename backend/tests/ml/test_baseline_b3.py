"""B3 基线冒烟：tiny_npz 上训练并产出报告。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from train_baseline_b3 import run  # noqa: E402


def test_b3_smoke(tiny_npz, tmp_path):
    report = run(str(tiny_npz), str(tmp_path), n_estimators=10)
    assert (tmp_path / "b3_extratrees.joblib").exists()
    assert report["n_test"] == 4
    assert "fs_ghz" in report["mae"]
    assert report["fs_mae_mhz"] >= 0
