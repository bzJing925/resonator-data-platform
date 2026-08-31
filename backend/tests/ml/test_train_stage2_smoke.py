"""Stage 2 训练冒烟。"""

import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from train_stage2_surrogate import Stage2Dataset, train  # noqa: E402


def test_stage2_dataset_shapes(tiny_npz):
    ds = Stage2Dataset(tiny_npz, "train")
    item = ds[0]
    assert item["cond"].shape == (5,)
    assert item["samples"].shape == (32, 2)
    assert item["target"].shape == (101,)
    assert item["z_baseline"].shape == (101,)


def test_train_smoke(tiny_npz, tmp_path):
    result = train(str(tiny_npz), str(tmp_path / "ck2"), batch_size=8, smoke=True)
    assert (tmp_path / "ck2" / "stage2.pt").exists()
    state = torch.load(tmp_path / "ck2" / "stage2.pt", map_location="cpu", weights_only=True)
    assert len(state) > 0
    assert result["best_val"] == result["best_val"]
