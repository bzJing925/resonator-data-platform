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
    """加载 stage1.pt + stage2.pt + baseline.npz + meta.json 的推理器。

    可选 Zp 校正器（zp_corrector.joblib，train_zp_corrector.py 产物）：
    存在时对输出谱峰区施加窄高斯加性校正，使顶点匹配树模型预测
    （级联 Zp 受 MLP 平滑限制 ~3dB，ExtraTrees 结构→Zp 可达 ~0.6dB）。
    不传路径时自动探测 stage1 同目录。
    """

    def __init__(
        self,
        stage1_path: str | Path,
        stage2_path: str | Path,
        stage2_config: str | Path,
        baseline_path: str | Path,
        meta_path: str | Path,
        device: torch.device | None = None,
        zp_corrector_path: str | Path | None = None,
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

        if zp_corrector_path is None:
            auto = Path(stage1_path).parent / "zp_corrector.joblib"
            zp_corrector_path = auto if auto.exists() else None
        self.zp_corrector = None
        if zp_corrector_path is not None:
            import joblib

            self.zp_corrector = joblib.load(zp_corrector_path)

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

        # Stage 2：稀疏点 + cond → 全谱（cond/z 归一化，与 Step B 训练一致）
        pm_c = self.p_mean[_COND_IDX]
        ps_c = self.p_std[_COND_IDX]
        cond = (
            (torch.tensor(params[_COND_IDX], dtype=torch.float32, device=self.dev) - pm_c) / ps_c
        ).unsqueeze(0)
        sample_freq = self.freq[pos]
        sparse_z_n = (sparse_z - self.z_mean) / self.z_std
        samples = (
            torch.from_numpy(np.stack([sample_freq, sparse_z_n], axis=-1))
            .float()
            .unsqueeze(0)
            .to(self.dev)
        )
        zb = (
            torch.from_numpy(baseline_interpolate(sample_freq, sparse_z_n, self.freq))
            .float()
            .unsqueeze(0)
            .to(self.dev)
        )
        freq_t = torch.from_numpy(self.freq).float().to(self.dev)
        z_pred = self.stage2(cond, samples, freq_t, z_baseline=zb)[0].cpu().numpy()
        z_pred = z_pred * self.z_std + self.z_mean

        zp_tree_db, zp_corr_db = None, None
        if self.zp_corrector is not None:
            zp_tree_db = float(
                self.zp_corrector.predict(np.array([[eg, fl, area_um2, pf]]))[0]
            )
            win = (self.freq >= fp - 0.05) & (self.freq <= fp + 0.05)
            if win.any():
                apex_f = float(self.freq[win][int(np.argmax(z_pred[win]))])
                zp_corr_db = zp_tree_db - float(z_pred[win].max())
                # σ=12MHz 窄高斯：只抬顶点邻域，不扰动其余谱形
                z_pred = z_pred + zp_corr_db * np.exp(
                    -0.5 * ((self.freq - apex_f) / 0.012) ** 2
                )

        return {
            "params": {c: float(params[i]) for i, c in enumerate(_PARAM_COLS)},
            "freq_ghz": self.freq,
            "z_db": z_pred,
            "sample_freq": sample_freq,
            "sample_z": sparse_z,
            "zp_tree_db": zp_tree_db,
            "zp_correction_db": zp_corr_db,
        }
