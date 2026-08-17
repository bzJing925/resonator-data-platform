"""Stage 1 训练冒烟：2 epoch 跑通且产出 checkpoint、loss 有限。"""

import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from train_surrogate_stage1 import train  # noqa: E402


def test_train_smoke(tiny_npz, tmp_path):
    result = train(str(tiny_npz), str(tmp_path / "ckpt"), smoke=True)
    ckpt = torch.load(tmp_path / "ckpt" / "stage1.pt", map_location="cpu", weights_only=True)
    assert "model_state" in ckpt
    assert ckpt["k"] == 32
    assert result["best_val"] == result["best_val"]  # 非 NaN
    assert result["epochs_run"] <= 2
