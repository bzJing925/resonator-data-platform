"""代理模型 API 测试：status/predict 契约 + 产物缺失降级。"""

from __future__ import annotations

import json

import numpy as np
import pytest
import torch

import app.api.surrogate as surr
from app.config import Settings
from app.ml.sparse.reconstructor import SparseReconstructor
from app.ml.surrogate.model import BaselineResidualMLP


@pytest.fixture(autouse=True)
def _reset_surrogate_module():
    """每个测试重置路由模块的懒加载单例与缓存。"""
    surr._bundle = None
    surr._load_error = None
    surr._predict_cached.cache_clear()
    yield
    surr._bundle = None
    surr._load_error = None
    surr._predict_cached.cache_clear()


def _make_artifacts(data_dir, ckpt_dir, n_freq=101, k=32):
    data_dir.mkdir(parents=True, exist_ok=True)
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    s1 = BaselineResidualMLP(in_dim=5, hidden=32, n_res_blocks=1, n_params=7, k=k)
    torch.save(
        {"model_state": s1.state_dict(), "in_dim": 5, "hidden": 32,
         "n_res_blocks": 1, "n_params": 7, "k": k},
        ckpt_dir / "stage1.pt",
    )
    s2 = SparseReconstructor(d_model=32, n_encoder_layers=1, n_heads=2, dropout=0.0)
    torch.save(s2.state_dict(), ckpt_dir / "stage2_ft.pt")
    (ckpt_dir / "stage2_config.json").write_text(
        json.dumps({"d_model": 32, "n_encoder_layers": 1, "n_heads": 2, "dropout": 0.0,
                    "max_freq": 0.1}),
        encoding="utf-8",
    )

    freq = np.linspace(4.0, 7.0, n_freq)
    np.savez_compressed(
        data_dir / "baseline.npz",
        pfs=np.array([0.0, 1.0]),
        areas=np.array([700.0, 1500.0]),
        params_base=np.tile(np.array([4.8, 5.1, 7.0, 500, 400, 450, 380]), (2, 2, 1)),
        z_base_full=np.full((2, 2, n_freq), 40.0),
    )
    (data_dir / "meta.json").write_text(
        json.dumps({
            "freq_ghz": freq.tolist(), "k": k,
            "x_mean": [0.5] * 5, "x_std": [1.0] * 5,
            "params_mean": [4.85, 5.15, 7.0, 500, 400, 450, 380],
            "params_std": [0.02, 0.02, 0.5, 50, 40, 45, 38],
            "z_mean": [42.0], "z_std": [3.0],
        }),
        encoding="utf-8",
    )
    rng = np.random.default_rng(0)
    z = 40 + 5 * np.sin(freq * 2)
    np.savez_compressed(
        data_dir / "dataset.npz",
        x_struct=np.array([[1.0, 0.5, 1.0, 1500, 0], [1.0, 0.5, 1.0, 700, 0]],
                          dtype=np.float32),
        y_params=np.array([[4.81, 5.11, 7.0, 510, 400, 450, 380],
                           [4.79, 5.09, 7.0, 490, 400, 450, 380]], dtype=np.float32),
        full_z=np.stack([z + rng.normal(0, 0.1, n_freq), z + rng.normal(0, 0.1, n_freq)]
                        ).astype(np.float32),
    )


@pytest.fixture()
def surrogate_settings(tmp_path, monkeypatch):
    data_dir, ckpt_dir = tmp_path / "surr_data", tmp_path / "surr_ckpt"
    _make_artifacts(data_dir, ckpt_dir)
    test_settings = Settings(
        SURROGATE_DATA_DIR=data_dir,
        SURROGATE_CKPT_DIR=ckpt_dir,
        WATCH_ENABLED=False,
    )
    monkeypatch.setattr("app.api.surrogate.get_settings", lambda: test_settings)
    return test_settings


class TestSurrogateApi:
    def test_status_available(self, client, surrogate_settings):
        r = client.get("/api/surrogate/status")
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is True
        assert body["areas"] == [700, 1500]

    def test_predict_contract(self, client, surrogate_settings):
        r = client.get("/api/surrogate/predict",
                       params={"eg": 1.0, "fl": 0.5, "area": 1500, "pf": 0})
        assert r.status_code == 200
        body = r.json()
        assert len(body["z"]) == 101
        assert len(body["freq"]) == 101
        assert len(body["sample_freq"]) == 32
        assert set(body["params"]) == {"fs_ghz", "fp_ghz", "k2eff_pct", "qs", "qp",
                                       "qs_bodeq", "qp_bodeq"}
        assert body["in_grid"] is True
        assert len(body["true_curves"]) == 1  # 该格（1500）仅 1 条记录，另一条在 700 格
        assert abs(body["true_mean"]["fs_ghz"] - 4.81) < 0.02

    def test_predict_out_of_grid(self, client, surrogate_settings):
        r = client.get("/api/surrogate/predict",
                       params={"eg": 9.0, "fl": 9.0, "area": 1500, "pf": 0})
        assert r.status_code == 200
        assert r.json()["in_grid"] is False

    def test_unavailable_when_missing(self, client, tmp_path, monkeypatch):
        test_settings = Settings(
            SURROGATE_DATA_DIR=tmp_path / "none_data",
            SURROGATE_CKPT_DIR=tmp_path / "none_ckpt",
            WATCH_ENABLED=False,
        )
        monkeypatch.setattr("app.api.surrogate.get_settings", lambda: test_settings)
        r = client.get("/api/surrogate/status")
        assert r.status_code == 200
        assert r.json()["available"] is False
        r2 = client.get("/api/surrogate/predict",
                        params={"eg": 1.0, "fl": 0.5, "area": 1500})
        assert r2.status_code == 503
