"""代理模型 torch → ONNX 导出 + 对齐校验（规格 §5.1）。

用法：
    uv run python scripts/export_surrogate_onnx.py \
        --ckpt ./checkpoints_surrogate --data ./surrogate_data \
        --out ../frontend/public/webdata
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.ml.sparse.reconstructor import SparseReconstructor  # noqa: E402
from app.ml.surrogate.model import BaselineResidualMLP  # noqa: E402


class _Stage1Wrapped(nn.Module):
    """dict 输出 → 元组（ONNX 导出约束）。"""

    def __init__(self, m: BaselineResidualMLP) -> None:
        super().__init__()
        self.m = m

    def forward(self, x, params_base, sparse_base):
        out = self.m(x, params_base, sparse_base)
        return out["params"], out["sparse"]


def export_stage1(model: BaselineResidualMLP, out_path: str | Path, k: int) -> Path:
    w = _Stage1Wrapped(model).eval().requires_grad_(False)
    args = (torch.zeros(1, 5), torch.zeros(1, 7), torch.zeros(1, k))
    with torch.no_grad():
        torch.onnx.export(
            w,
            args,
            str(out_path),
            opset_version=17,
            dynamo=False,
            input_names=["x", "params_base", "sparse_base"],
            output_names=["params", "sparse"],
            dynamic_axes={
                "x": {0: "b"},
                "params_base": {0: "b"},
                "sparse_base": {0: "b"},
                "params": {0: "b"},
                "sparse": {0: "b"},
            },
        )
    return Path(out_path)


def export_stage2(model: SparseReconstructor, out_path: str | Path, k: int, n_freq: int) -> Path:
    model = model.eval().requires_grad_(False)

    class _W(nn.Module):
        def forward(self, cond, samples, freq_target, z_baseline):
            return model(cond, samples, freq_target, z_baseline=z_baseline)

    args = (torch.zeros(1, 5), torch.zeros(1, k, 2), torch.zeros(1, n_freq), torch.zeros(1, n_freq))
    # 关掉 MHA 融合 fastpath：eval 模式下 nn.TransformerEncoderLayer 会走
    # aten::_transformer_encoder_layer_fwd（无 ONNX symbolic）；关掉后走可导出的慢路径。
    torch.backends.mha.set_fastpath_enabled(False)
    try:
        with torch.no_grad():
            torch.onnx.export(
                _W(),
                args,
                str(out_path),
                opset_version=17,
                dynamo=False,
                input_names=["cond", "samples", "freq_target", "z_baseline"],
                output_names=["z"],
                dynamic_axes={
                    "cond": {0: "b"},
                    "samples": {0: "b"},
                    "freq_target": {0: "b"},
                    "z_baseline": {0: "b"},
                    "z": {0: "b"},
                },
            )
    finally:
        torch.backends.mha.set_fastpath_enabled(True)
    return Path(out_path)


def _align_check(
    ckpt_dir: Path,
    data_dir: Path,
    s1_onnx: Path,
    s2_onnx: Path,
    stage2_path: Path,
    n_cases: int = 100,
    tol: float = 1e-3,
) -> None:
    """真实数据对齐：torch vs onnxruntime（归一化空间，1e-3 容差）。"""
    import onnxruntime as ort

    meta = json.loads((data_dir / "meta.json").read_text(encoding="utf-8"))
    with np.load(data_dir / "dataset.npz") as z:
        x_struct = z["x_struct"][:n_cases]
        y_sparse = z["y_sparse"][:n_cases]
        y_params = z["y_params"][:n_cases]
        params_base = z["params_base"][:n_cases]
        sparse_base = z["sparse_base"][:n_cases]
        sample_pos = z["sample_pos"][:n_cases]
    freq = np.asarray(meta["freq_ghz"], dtype=np.float32)
    x_mean = np.asarray(meta["x_mean"], dtype=np.float32)
    x_std = np.asarray(meta["x_std"], dtype=np.float32)
    p_mean = np.asarray(meta["params_mean"], dtype=np.float32)
    p_std = np.asarray(meta["params_std"], dtype=np.float32)
    zm, zs = float(meta["z_mean"][0]), float(meta["z_std"][0])

    x_n = ((x_struct - x_mean) / x_std).astype(np.float32)
    pb_n = ((params_base - p_mean) / p_std).astype(np.float32)
    sb_n = ((sparse_base - zm) / zs).astype(np.float32)

    s1 = ort.InferenceSession(str(s1_onnx))
    p_o, s_o = s1.run(None, {"x": x_n, "params_base": pb_n, "sparse_base": sb_n})
    c1 = torch.load(ckpt_dir / "stage1.pt", map_location="cpu", weights_only=True)
    m1 = BaselineResidualMLP(
        in_dim=c1["in_dim"],
        hidden=c1["hidden"],
        n_res_blocks=c1["n_res_blocks"],
        n_params=c1["n_params"],
        k=c1["k"],
    ).eval()
    m1.load_state_dict(c1["model_state"])
    with torch.no_grad():
        ref = m1(torch.from_numpy(x_n), torch.from_numpy(pb_n), torch.from_numpy(sb_n))
    np.testing.assert_allclose(p_o, ref["params"].numpy(), atol=tol)
    np.testing.assert_allclose(s_o, ref["sparse"].numpy(), atol=tol)

    # Stage 2：真值稀疏点 + 样条基线（归一化空间，与 Step A/B 训练一致）
    from app.core.sparse_sampler import baseline_interpolate

    cond_idx = [0, 1, 3, 4, 2]
    cond = ((y_params[:, cond_idx] - p_mean[cond_idx]) / p_std[cond_idx]).astype(np.float32)
    sfreq = freq[sample_pos]
    sz_n = ((y_sparse - zm) / zs).astype(np.float32)
    samples = np.stack([sfreq, sz_n], axis=-1).astype(np.float32)
    zb = np.stack(
        [baseline_interpolate(sfreq[i], sz_n[i], freq) for i in range(len(sfreq))]
    ).astype(np.float32)
    ft = np.tile(freq, (len(samples), 1))
    s2 = ort.InferenceSession(str(s2_onnx))
    (z_o,) = s2.run(None, {"cond": cond, "samples": samples, "freq_target": ft, "z_baseline": zb})
    cfg = json.loads((ckpt_dir / "stage2_config.json").read_text(encoding="utf-8"))
    m2 = SparseReconstructor(**cfg).eval()
    m2.load_state_dict(torch.load(stage2_path, map_location="cpu", weights_only=True))
    with torch.no_grad():
        ref2 = m2(
            torch.from_numpy(cond),
            torch.from_numpy(samples),
            torch.from_numpy(ft),
            z_baseline=torch.from_numpy(zb),
        )
    np.testing.assert_allclose(z_o, ref2.numpy(), atol=tol)
    print(f"对齐校验通过（{len(x_n)} 例，容差 {tol}）")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", default="./checkpoints_surrogate")
    ap.add_argument("--data", default="./surrogate_data")
    ap.add_argument("--out", default=str(PROJECT_ROOT.parent / "frontend/public/webdata"))
    args = ap.parse_args()
    ckpt, data, out = Path(args.ckpt), Path(args.data), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    c1 = torch.load(ckpt / "stage1.pt", map_location="cpu", weights_only=True)
    m1 = BaselineResidualMLP(
        in_dim=c1["in_dim"],
        hidden=c1["hidden"],
        n_res_blocks=c1["n_res_blocks"],
        n_params=c1["n_params"],
        k=c1["k"],
    ).eval()
    m1.load_state_dict(c1["model_state"])
    cfg = json.loads((ckpt / "stage2_config.json").read_text(encoding="utf-8"))
    m2 = SparseReconstructor(**cfg).eval()
    stage2_pt = ckpt / "stage2_ft.pt"
    if not stage2_pt.exists():
        stage2_pt = ckpt / "stage2.pt"
    m2.load_state_dict(torch.load(stage2_pt, map_location="cpu", weights_only=True))
    meta = json.loads((data / "meta.json").read_text(encoding="utf-8"))

    s1 = export_stage1(m1, out / "stage1.onnx", k=c1["k"])
    s2 = export_stage2(m2, out / "stage2.onnx", k=meta["k"], n_freq=meta["n_freq"])
    _align_check(ckpt, data, s1, s2, stage2_pt)
    from app.services.model_metadata import artifact_digest, describe
    metadata = describe(data, ckpt, ckpt.parent / 'eval_out')
    metadata.source_model_digest = metadata.model_digest
    metadata.model_digest = artifact_digest([('stage1', s1), ('stage2', s2)])
    (out / 'model_metadata.json').write_text(metadata.model_dump_json(indent=2))
    print(f"ONNX 已导出并校验: {s1} , {s2}")


if __name__ == "__main__":
    main()
