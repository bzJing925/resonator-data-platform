"""数据集定义：合成谐振器频谱 + 真实 .s1p 数据加载。

合成模式用于无真实数据时快速验证训练流程；
真实模式通过 SQLAlchemy 从数据库批量加载器件参数和 s_param_path；
S1P 批量模式直接解析文件系统中的 .s1p 文件。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import torch
from torch.utils.data import Dataset

if TYPE_CHECKING:
    from numpy.typing import NDArray


# ---------------------------------------------------------------------------
# 合成数据生成
# ---------------------------------------------------------------------------


def _synth_gaussian_peak(
    freq: NDArray[np.float64],
    center: float,
    amplitude: float,
    width: float,
) -> NDArray[np.float64]:
    """高斯峰/谷。"""
    return amplitude * np.exp(-((freq - center) ** 2) / (2 * width**2))


def generate_synthetic_spectrum(
    n_freq: int = 1001,
    f_start: float = 1.0,
    f_end: float = 3.0,
    fs: float = 1.8,
    fp: float = 2.2,
    noise_std: float = 0.02,
    rng: np.random.Generator | None = None,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """生成一条模拟 S11_dB 频谱。

    物理模型：
    - 基线：50Ω 匹配下的平坦响应（≈ -40dB）
    - fs 处：串联谐振 → S11 极小值（深谷）
    - fp 处：并联谐振 → S11 极大值（高峰）
    - 同 batch 共享基线 + 批量偏移
    - 器件级随机扰动体现在 fs/fp 偏移和峰深变化

    Args:
        n_freq: 频点数。
        f_start, f_end: 频率范围（GHz）。
        fs: 串联谐振频率（GHz）。
        fp: 并联谐振频率（GHz）。
        noise_std: 加性高斯噪声标准差。
        rng: 随机数生成器。

    Returns:
        (freq, spectrum) — freq 为频率轴 (GHz)，spectrum 为 S11_dB。
    """
    if rng is None:
        rng = np.random.default_rng()

    freq = np.linspace(f_start, f_end, n_freq)

    # 基线（50Ω 匹配，S11 ≈ -40dB）
    baseline = -40.0 * np.ones_like(freq)

    # fs 处深谷（串联谐振，阻抗最小，反射最小）
    # 谷深随机在 -20 ~ -35 dB 之间
    fs_depth = rng.uniform(-35.0, -20.0)
    fs_width = rng.uniform(0.03, 0.08)
    valley = _synth_gaussian_peak(freq, fs, fs_depth - (-40.0), fs_width)

    # fp 处高峰（并联谐振，阻抗最大，反射最大）
    # 峰高随机在 -10 ~ -5 dB 之间
    fp_height = rng.uniform(-10.0, -5.0)
    fp_width = rng.uniform(0.04, 0.10)
    peak = _synth_gaussian_peak(freq, fp, fp_height - (-40.0), fp_width)

    # 加性噪声
    noise = rng.normal(0.0, noise_std, size=freq.shape)

    spectrum = baseline + valley + peak + noise
    return freq, spectrum


def generate_synthetic_batch(
    n_devices: int = 200,
    n_freq: int = 1001,
    batch_id: int = 0,
    rng: np.random.Generator | None = None,
) -> list[dict]:
    """生成一个 batch 的合成数据。

    同 batch 的器件共享压电层厚度 → fs/fp 基线相同，
    差异来自微结构（面积、位置）导致的局部偏移。

    Returns:
        器件列表，每个元素为 dict，含：
        - spectrum: NDArray (n_freq,)
        - params: NDArray (6,) — [area_um2, x, y, eg, fl, ag]
        - fs_ghz, fp_ghz: float
        - batch_id: int
    """
    if rng is None:
        rng = np.random.default_rng(batch_id)

    # batch 级公共基线（压电层厚度决定）
    base_fs = rng.normal(1.85, 0.05)
    base_fp = base_fs + rng.uniform(0.25, 0.45)

    devices: list[dict] = []
    for _i in range(n_devices):
        # 器件级偏移（微结构差异）
        fs_offset = rng.normal(0.0, 0.02)
        fp_offset = rng.normal(0.0, 0.03)
        fs = base_fs + fs_offset
        fp = base_fp + fp_offset

        # 生成频谱
        _, spectrum = generate_synthetic_spectrum(
            n_freq=n_freq, fs=fs, fp=fp, rng=rng
        )

        # 器件参数（归一化前原始值）
        area_um2 = int(rng.integers(100, 5000))
        x = int(rng.integers(0, 30))
        y = int(rng.integers(0, 30))
        eg = float(rng.uniform(0.5, 2.0))
        fl = float(rng.uniform(0.1, 1.0))
        ag = float(rng.uniform(0.0, 0.5))

        devices.append({
            "spectrum": spectrum.astype(np.float32),
            "params": np.array([area_um2, x, y, eg, fl, ag], dtype=np.float32),
            "fs_ghz": float(fs),
            "fp_ghz": float(fp),
            "batch_id": batch_id,
        })

    return devices


# ---------------------------------------------------------------------------
# 数据集类
# ---------------------------------------------------------------------------


class SyntheticSpectrumDataset(Dataset):
    """合成频谱数据集。

    生成多个 batch，每个 batch 内有公共基线 + 器件级偏移。
    自动选每 batch 的基准器件并预计算 z_base。

    Args:
        n_batches: batch 数量。
        n_devices_per_batch: 每 batch 器件数。
        n_freq: 频点数。
        latent_dim: VAE latent 维度（用于预计算 z_base）。
    """

    def __init__(
        self,
        n_batches: int = 10,
        n_devices_per_batch: int = 200,
        n_freq: int = 1001,
        latent_dim: int = 12,
    ) -> None:
        super().__init__()
        self.n_freq = n_freq
        self.latent_dim = latent_dim
        self.devices: list[dict] = []
        self.batch_meta: dict[int, dict] = {}

        for b in range(n_batches):
            batch_devices = generate_synthetic_batch(
                n_devices=n_devices_per_batch,
                n_freq=n_freq,
                batch_id=b,
                rng=np.random.default_rng(42 + b),
            )
            self.devices.extend(batch_devices)

            # 选基准：fs 中位数 + 面积中位数
            fs_vals = [d["fs_ghz"] for d in batch_devices]
            fs_median = float(np.median(fs_vals))
            sorted_by_fs = sorted(
                batch_devices,
                key=lambda d: abs(d["fs_ghz"] - fs_median),
            )
            candidates = sorted_by_fs[: max(1, len(sorted_by_fs) // 5)]
            area_vals = [d["params"][0] for d in candidates]
            area_median = float(np.median(area_vals))
            base = min(
                candidates,
                key=lambda d: abs(d["params"][0] - area_median),
            )
            self.batch_meta[b] = {
                "base_spectrum": base["spectrum"],
                "base_params": base["params"],
                "base_fs": base["fs_ghz"],
                "base_fp": base["fp_ghz"],
            }

        # 全局归一化参数（用于 params）
        all_params = np.stack([d["params"] for d in self.devices])
        self.params_mean = torch.from_numpy(all_params.mean(axis=0)).float()
        self.params_std = torch.from_numpy(all_params.std(axis=0)).float()
        self.params_std = torch.clamp(self.params_std, min=1e-6)

    def __len__(self) -> int:
        return len(self.devices)

    def __getitem__(self, idx: int) -> dict[str, torch.Tensor]:
        d = self.devices[idx]
        batch_id = d["batch_id"]
        meta = self.batch_meta[batch_id]

        spectrum = torch.from_numpy(d["spectrum"]).float().unsqueeze(0)
        params = torch.from_numpy(d["params"]).float()
        params_norm = (params - self.params_mean) / self.params_std

        base_spectrum = torch.from_numpy(meta["base_spectrum"]).float().unsqueeze(0)

        return {
            "spectrum": spectrum,
            "params": params_norm,
            "fs": torch.tensor(d["fs_ghz"], dtype=torch.float32),
            "fp": torch.tensor(d["fp_ghz"], dtype=torch.float32),
            "batch_id": torch.tensor(batch_id, dtype=torch.long),
            "base_spectrum": base_spectrum,
            "base_fs": torch.tensor(meta["base_fs"], dtype=torch.float32),
            "base_fp": torch.tensor(meta["base_fp"], dtype=torch.float32),
        }


class RealSpectrumDataset(Dataset):
    """真实频谱数据集（从数据库 + 文件系统加载）。

    需要 PostgreSQL 运行且 .s1p 文件存在于 DATA_ROOT/files/ 下。
    当前项目无现成数据，此接口预留供后续接入。

    Args:
        db_url: PostgreSQL 连接字符串。
        data_root: 数据根目录（包含 files/ 子目录）。
        n_freq: 统一频点数。
        batch_ids: 指定加载的 batch ID 列表，None 表示全部。
    """

    def __init__(
        self,
        db_url: str = "postgresql+psycopg://aln:aln@localhost:5432/aln",
        data_root: str | Path = "/data3/aln",
        n_freq: int = 1001,
        batch_ids: list[int] | None = None,
    ) -> None:
        super().__init__()
        self.n_freq = n_freq
        self.data_root = Path(data_root)
        self.devices: list[dict] = []
        self.batch_meta: dict[int, dict] = {}

        # 延迟导入，避免无 sqlalchemy 时报错
        try:
            from sqlalchemy import create_engine, select

            from app.models import Batch, Device
        except ImportError as exc:
            raise ImportError(
                "真实数据模式需要 sqlalchemy 和 app 模块。"
                "请安装依赖后重试，或使用 --mode synthetic。"
            ) from exc

        engine = create_engine(db_url)
        with engine.connect() as conn:
            # 加载指定 batch
            stmt = select(Batch.id, Batch.batch_no)
            if batch_ids:
                stmt = stmt.where(Batch.id.in_(batch_ids))
            batches = conn.execute(stmt).all()

            for batch_id, batch_no in batches:
                # 加载该 batch 的全部 device
                device_stmt = select(Device).where(Device.batch_id == batch_id)
                rows = conn.execute(device_stmt).mappings().all()

                batch_devices = []
                for row in rows:
                    s_param_path = row["s_param_path"]
                    if not s_param_path:
                        continue

                    full_path = self.data_root / "files" / batch_no / s_param_path
                    if not full_path.exists():
                        continue

                    try:
                        import skrf as rf
                        net = rf.Network(str(full_path))
                        # 取 S11_dB
                        s11 = net.s_db[:, 0, 0] if net.nports > 1 else net.s_db[:]
                        # 插值到统一长度
                        if len(s11) != n_freq:
                            old_x = np.linspace(0, 1, len(s11))
                            new_x = np.linspace(0, 1, n_freq)
                            s11 = np.interp(new_x, old_x, s11)
                        spectrum = s11.astype(np.float32)
                    except Exception:
                        continue

                    params = np.array([
                        row.get("area_um2") or 0,
                        row.get("x") or 0,
                        row.get("y") or 0,
                        row.get("eg") or 0.0,
                        row.get("fl") or 0.0,
                        row.get("ag") or 0.0,
                    ], dtype=np.float32)

                    batch_devices.append({
                        "spectrum": spectrum,
                        "params": params,
                        "fs_ghz": row.get("fs_ghz") or 0.0,
                        "fp_ghz": row.get("fp_ghz") or 0.0,
                        "batch_id": batch_id,
                    })

                if not batch_devices:
                    continue

                self.devices.extend(batch_devices)

                # 选基准
                from app.ml.utils import select_base_device
                base = select_base_device(batch_devices)
                self.batch_meta[batch_id] = {
                    "base_spectrum": base["spectrum"],
                    "base_params": base["params"],
                    "base_fs": base.get("fs_ghz", 0.0),
                    "base_fp": base.get("fp_ghz", 0.0),
                }

        # 全局归一化
        if self.devices:
            all_params = np.stack([d["params"] for d in self.devices])
            self.params_mean = torch.from_numpy(all_params.mean(axis=0)).float()
            self.params_std = torch.from_numpy(all_params.std(axis=0)).float()
            self.params_std = torch.clamp(self.params_std, min=1e-6)
        else:
            self.params_mean = torch.zeros(6)
            self.params_std = torch.ones(6)

    def __len__(self) -> int:
        return len(self.devices)

    def __getitem__(self, idx: int) -> dict[str, torch.Tensor]:
        d = self.devices[idx]
        batch_id = d["batch_id"]
        meta = self.batch_meta[batch_id]

        spectrum = torch.from_numpy(d["spectrum"]).float().unsqueeze(0)
        params = torch.from_numpy(d["params"]).float()
        params_norm = (params - self.params_mean) / self.params_std

        base_spectrum = torch.from_numpy(meta["base_spectrum"]).float().unsqueeze(0)

        return {
            "spectrum": spectrum,
            "params": params_norm,
            "fs": torch.tensor(d["fs_ghz"], dtype=torch.float32),
            "fp": torch.tensor(d["fp_ghz"], dtype=torch.float32),
            "batch_id": torch.tensor(batch_id, dtype=torch.long),
            "base_spectrum": base_spectrum,
            "base_fs": torch.tensor(meta["base_fs"], dtype=torch.float32),
            "base_fp": torch.tensor(meta["base_fp"], dtype=torch.float32),
        }


# ---------------------------------------------------------------------------
# S1P 批量数据集（直接解析文件系统）
# ---------------------------------------------------------------------------


class RealS1PBatchDataset(Dataset):
    """从文件系统直接加载一批 .s1p 文件的数据集。

    整批视为 batch_id=0，自动选基准器件并预计算 z_base。

    Args:
        s1p_dir: 包含 .s1p 文件的目录。
        n_freq: 插值后的目标频点数（默认 1001）。
        latent_dim: VAE latent 维度（预留）。
        noise_std: 数据增强噪声标准差（默认 0.0）。
    """

    def __init__(
        self,
        s1p_dir: str | Path,
        n_freq: int = 1001,
        latent_dim: int = 12,
        noise_std: float = 0.0,
        group_by_area: bool = False,
    ) -> None:
        super().__init__()
        self.n_freq = n_freq
        self.latent_dim = latent_dim
        self.noise_std = noise_std
        self.devices: list[dict] = []
        self.batch_meta: dict[int, dict] = {}

        import re

        from app.ml.filename_parser import parse_filename_params
        from app.ml.s1p_parser import parse_s1p

        s1p_dir = Path(s1p_dir)
        files = sorted(s1p_dir.glob("*.s1p"))
        if not files:
            raise ValueError(f"{s1p_dir} 下未找到任何 .s1p 文件")

        print(f"加载 {len(files)} 个 .s1p 文件 from {s1p_dir}")

        # Area 字母映射
        area_to_id = {c: i for i, c in enumerate("ABCDE")}

        for f in files:
            try:
                freq_hz, s11_db = parse_s1p(f, target_n_freq=n_freq)
            except Exception as exc:
                print(f"[警告] 跳过 {f.name}: {exc}")
                continue

            params = parse_filename_params(f.name)
            param_vec = np.array([
                params.get("area_um2", 0),
                params.get("x", 0),
                params.get("y", 0),
                params.get("eg", 0.0),
                params.get("fl", 0.0),
                params.get("ag", 0.0),
            ], dtype=np.float32)

            # 从频谱中检测 fs/fp（最小/最大 S11 位置）
            fs_idx = int(np.argmin(s11_db))
            fp_idx = int(np.argmax(s11_db))
            fs_ghz = float(freq_hz[fs_idx] / 1e9)
            fp_ghz = float(freq_hz[fp_idx] / 1e9)

            # 确定 batch_id
            if group_by_area:
                m = re.match(r"S22_3_([A-Z])", f.name)
                area_letter = m.group(1) if m else "A"
                batch_id = area_to_id.get(area_letter, 0)
            else:
                batch_id = 0

            self.devices.append({
                "spectrum": s11_db.astype(np.float32),
                "params": param_vec,
                "fs_ghz": fs_ghz,
                "fp_ghz": fp_ghz,
                "batch_id": batch_id,
                "filename": f.name,
            })

        if not self.devices:
            raise ValueError("未成功加载任何 .s1p 文件")

        # 按 batch_id 分组选基准
        batch_ids = sorted({d["batch_id"] for d in self.devices})
        for bid in batch_ids:
            group = [d for d in self.devices if d["batch_id"] == bid]
            mean_spectra = [float(np.mean(d["spectrum"])) for d in group]
            median_mean = float(np.median(mean_spectra))
            base_idx = int(np.argmin([abs(m - median_mean) for m in mean_spectra]))
            base = group[base_idx]

            self.batch_meta[bid] = {
                "base_spectrum": base["spectrum"],
                "base_params": base["params"],
                "base_fs": base["fs_ghz"],
                "base_fp": base["fp_ghz"],
                "base_filename": base["filename"],
            }
            area_name = chr(ord("A") + bid) if bid < 5 else str(bid)
            print(
                f"  Batch {area_name} (n={len(group)}): 基准 {base['filename']} "
                f"(fs={base['fs_ghz']:.3f}GHz, fp={base['fp_ghz']:.3f}GHz)"
            )

        # 全局归一化
        all_params = np.stack([d["params"] for d in self.devices])
        self.params_mean = torch.from_numpy(all_params.mean(axis=0)).float()
        self.params_std = torch.from_numpy(all_params.std(axis=0)).float()
        self.params_std = torch.clamp(self.params_std, min=1e-6)

    def __len__(self) -> int:
        return len(self.devices)

    def __getitem__(self, idx: int) -> dict[str, torch.Tensor]:
        d = self.devices[idx]
        batch_id = d["batch_id"]
        meta = self.batch_meta[batch_id]

        spectrum = torch.from_numpy(d["spectrum"]).float().unsqueeze(0)
        if self.noise_std > 0.0:
            noise = torch.randn_like(spectrum) * self.noise_std
            spectrum = spectrum + noise

        params = torch.from_numpy(d["params"]).float()
        params_norm = (params - self.params_mean) / self.params_std

        base_spectrum = torch.from_numpy(meta["base_spectrum"]).float().unsqueeze(0)

        return {
            "spectrum": spectrum,
            "params": params_norm,
            "fs": torch.tensor(d["fs_ghz"], dtype=torch.float32),
            "fp": torch.tensor(d["fp_ghz"], dtype=torch.float32),
            "batch_id": torch.tensor(batch_id, dtype=torch.long),
            "base_spectrum": base_spectrum,
            "base_fs": torch.tensor(meta["base_fs"], dtype=torch.float32),
            "base_fp": torch.tensor(meta["base_fp"], dtype=torch.float32),
        }
