"""端到端推理：结构参数 → Stage 1（基准+残差）→ 稀疏点 → Stage 2 → 全谱。

Stage 1 需要两遍前向：第一遍出性能参数（含 fs/fp），
用预测的 fs/fp 按确定性规则生成采样位置并查 sparse_base，第二遍出稀疏点。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import torch

from app.config import AlgorithmConfig
from app.core.sparse_sampler import baseline_interpolate, deterministic_sample_positions
from app.ml.sparse.reconstructor import SparseReconstructor
from app.ml.surrogate.baseline import BaselineTable
from app.ml.surrogate.model import BaselineResidualMLP

_PARAM_COLS = list(AlgorithmConfig.surr_param_cols)
_COND_IDX = [0, 1, 3, 4, 2]  # [fs, fp, Qs, Qp, kt2]


class SurrogatePredictor:
    """加载 stage1.pt + stage2.pt + baseline.npz + meta.json 的推理器。"""

    def __init__(
        self,
        stage1_path: str | Path,
        stage2_path: str | Path,
        stage2_config: str | Path,
        baseline_path: str | Path,
        meta_path: str | Path,
        device: torch.device | None = None,
    ) -> None:
        self.dev = device or (
            torch.device("mps") if torch.backends.mps.is_available() else torch.device("cpu")
        )
        meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
        self.freq = np.asarray(meta["freq_ghz"], dtype=np.float64)
        self.k: int = meta["k"]
        self.x_mean = np.asarray(meta["x_mean"], dtype=np.float32)
        self.x_std = np.asarray(meta["x_std"], dtype=np.float32)
        self.p_mean = torch.tensor(meta["params_mean"], dtype=torch.float32, device=self.dev)
        self.p_std = torch.tensor(meta["params_std"], dtype=torch.float32, device=self.dev)
        self.z_mean = float(meta["z_mean"][0])
        self.z_std = float(meta["z_std"][0])
        self.baseline = BaselineTable.load(baseline_path)

        ckpt = torch.load(stage1_path, map_location=self.dev, weights_only=True)
        self.stage1 = BaselineResidualMLP(
            in_dim=ckpt["in_dim"],
            hidden=ckpt["hidden"],
            n_res_blocks=ckpt["n_res_blocks"],
            n_params=ckpt["n_params"],
            k=ckpt["k"],
        ).to(self.dev)
        self.stage1.load_state_dict(ckpt["model_state"])
        self.stage1.eval()

        cfg = json.loads(Path(stage2_config).read_text(encoding="utf-8"))
        self.stage2 = SparseReconstructor(**cfg).to(self.dev)
        self.stage2.load_state_dict(
            torch.load(stage2_path, map_location=self.dev, weights_only=True)
        )
        self.stage2.eval()

    @torch.no_grad()
    def predict(
        self,
        eg: float,
        fl: float,
        area_um2: float,
        pf: float = 0.0,
        ag: float = AlgorithmConfig.surr_default_ag,
    ) -> dict:
        x = (np.array([eg, fl, ag, area_um2, pf], dtype=np.float32) - self.x_mean) / self.x_std
        x_t = torch.from_numpy(x).float().unsqueeze(0).to(self.dev)

        params_base, z_base_full = self.baseline.lookup(area_um2, pf)
        pb = (torch.tensor(params_base, dtype=torch.float32, device=self.dev) - self.p_mean) / (
            self.p_std
        )
        pb = pb.unsqueeze(0)

        # 第一遍：性能参数（sparse_base 用零，params 头不依赖它）
        out1 = self.stage1(x_t, pb, torch.zeros(1, self.k, device=self.dev))
        params = (out1["params"][0] * self.p_std + self.p_mean).cpu().numpy()
        fs, fp = float(params[0]), float(params[1])

        # 预测 fs/fp → 采样位置 → sparse_base（dB → 归一化）
        pos = deterministic_sample_positions(self.freq, fs, fp, k=self.k)
        sparse_base_db = (
            z_base_full[pos]
            if len(z_base_full) == len(self.freq)
            else np.interp(
                pos / (len(self.freq) - 1), np.linspace(0, 1, len(z_base_full)), z_base_full
            )
        )
        sb = (
            torch.from_numpy((sparse_base_db - self.z_mean) / self.z_std)
            .float()
            .unsqueeze(0)
            .to(self.dev)
        )

        # 第二遍：稀疏点
        out2 = self.stage1(x_t, pb, sb)
        sparse_z = (out2["sparse"][0].cpu().numpy() * self.z_std + self.z_mean).astype(np.float64)

        # Stage 2：稀疏点 + cond → 全谱
        cond = torch.tensor(params[_COND_IDX], dtype=torch.float32, device=self.dev).unsqueeze(0)
        sample_freq = self.freq[pos]
        samples = (
            torch.from_numpy(np.stack([sample_freq, sparse_z], axis=-1))
            .float()
            .unsqueeze(0)
            .to(self.dev)
        )
        zb = (
            torch.from_numpy(baseline_interpolate(sample_freq, sparse_z, self.freq))
            .float()
            .unsqueeze(0)
            .to(self.dev)
        )
        freq_t = torch.from_numpy(self.freq).float().to(self.dev)
        z_pred = self.stage2(cond, samples, freq_t, z_baseline=zb)[0].cpu().numpy()

        return {
            "params": {c: float(params[i]) for i, c in enumerate(_PARAM_COLS)},
            "freq_ghz": self.freq,
            "z_db": z_pred,
            "sample_freq": sample_freq,
            "sample_z": sparse_z,
        }
