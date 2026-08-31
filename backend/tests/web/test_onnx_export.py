"""ONNX 导出契约：导出文件存在、输出形状/数值与 torch 一致。"""

import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from export_surrogate_onnx import export_stage1, export_stage2  # noqa: E402

from app.ml.sparse.reconstructor import SparseReconstructor  # noqa: E402
from app.ml.surrogate.model import BaselineResidualMLP  # noqa: E402


def test_stage1_export(tmp_path):
    m = BaselineResidualMLP(in_dim=5, hidden=32, n_res_blocks=1, n_params=7, k=32).eval()
    # 打破零初始化，确保残差通路也被校验
    with torch.no_grad():
        for p in m.parameters():
            p.add_(torch.randn_like(p) * 0.05)
    out = export_stage1(m, tmp_path / "s1.onnx", k=32)
    import onnxruntime as ort

    sess = ort.InferenceSession(str(out))
    x = np.random.default_rng(0).normal(size=(3, 5)).astype(np.float32)
    pb = np.zeros((3, 7), dtype=np.float32)
    sb = np.zeros((3, 32), dtype=np.float32)
    p_o, s_o = sess.run(None, {"x": x, "params_base": pb, "sparse_base": sb})
    with torch.no_grad():
        ref = m(torch.from_numpy(x), torch.from_numpy(pb), torch.from_numpy(sb))
    assert p_o.shape == (3, 7) and s_o.shape == (3, 32)
    np.testing.assert_allclose(p_o, ref["params"].numpy(), atol=1e-5)
    np.testing.assert_allclose(s_o, ref["sparse"].numpy(), atol=1e-5)


def test_stage2_export(tmp_path):
    m = SparseReconstructor(d_model=32, n_encoder_layers=1, n_heads=2, dropout=0.0).eval()
    out = export_stage2(m, tmp_path / "s2.onnx", k=32, n_freq=101)
    import onnxruntime as ort

    sess = ort.InferenceSession(str(out))
    rng = np.random.default_rng(1)
    cond = rng.normal(size=(2, 5)).astype(np.float32)
    samples = rng.normal(size=(2, 32, 2)).astype(np.float32)
    ft = np.tile(np.linspace(4.0, 7.0, 101, dtype=np.float32), (2, 1))
    zb = rng.normal(size=(2, 101)).astype(np.float32) + 40
    (o,) = sess.run(None, {"cond": cond, "samples": samples,
                           "freq_target": ft, "z_baseline": zb})
    with torch.no_grad():
        ref = m(torch.from_numpy(cond), torch.from_numpy(samples),
                torch.from_numpy(ft), z_baseline=torch.from_numpy(zb))
    assert o.shape == (2, 101)
    np.testing.assert_allclose(o, ref.numpy(), atol=1e-4)
