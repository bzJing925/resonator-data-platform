"""端到端推理契约测试：随机初始化小模型 → 输出形状/有限性/参数合理性。"""

import json

import numpy as np
import torch

from app.ml.sparse.reconstructor import SparseReconstructor
from app.ml.surrogate.infer import SurrogatePredictor
from app.ml.surrogate.model import BaselineResidualMLP


def _make_ckpts(tiny_npz, tmp_path):
    s1 = BaselineResidualMLP(in_dim=5, hidden=32, n_res_blocks=1, n_params=7, k=32)
    torch.save(
        {"model_state": s1.state_dict(), "in_dim": 5, "hidden": 32,
         "n_res_blocks": 1, "n_params": 7, "k": 32},
        tmp_path / "stage1.pt",
    )
    s2 = SparseReconstructor(d_model=32, n_encoder_layers=1, n_heads=2, dropout=0.0)
    torch.save(s2.state_dict(), tmp_path / "stage2.pt")
    (tmp_path / "stage2_config.json").write_text(
        json.dumps({"d_model": 32, "n_encoder_layers": 1, "n_heads": 2, "dropout": 0.0}),
        encoding="utf-8",
    )
    # 基准表：tiny_npz 不含 baseline.npz，造一个 (pf=2, area=2) 恒 40 的
    pfs = np.array([0.0, 1.0])
    areas = np.array([700.0, 1500.0])
    np.savez_compressed(
        tmp_path / "baseline.npz",
        pfs=pfs,
        areas=areas,
        params_base=np.tile(np.array([4.8, 5.1, 7.0, 500, 400, 450, 380]), (2, 2, 1)),
        z_base_full=np.full((2, 2, 101), 40.0),
    )
    return tmp_path


class TestSurrogatePredictor:
    def test_predict_contract(self, tiny_npz, tmp_path):
        ckpt = _make_ckpts(tiny_npz, tmp_path)
        pred = SurrogatePredictor(
            stage1_path=ckpt / "stage1.pt",
            stage2_path=ckpt / "stage2.pt",
            stage2_config=ckpt / "stage2_config.json",
            baseline_path=ckpt / "baseline.npz",
            meta_path=tiny_npz / "meta.json",
        )
        out = pred.predict(eg=1.0, fl=0.5, area_um2=1200, pf=0)
        assert out["z_db"].shape == (101,)
        assert out["freq_ghz"].shape == (101,)
        assert np.all(np.isfinite(out["z_db"]))
        assert set(out["params"]) == {"fs_ghz", "fp_ghz", "k2eff_pct", "qs", "qp",
                                      "qs_bodeq", "qp_bodeq"}
        assert out["sample_freq"].shape == (32,)
        assert np.all(np.diff(out["sample_freq"]) > 0)  # 采样点升序
