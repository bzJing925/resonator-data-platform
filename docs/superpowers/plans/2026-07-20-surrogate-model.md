# 谐振器代理模型（Surrogate Model）实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 训练两阶段代理模型：输入结构参数 (EG, FL, 面积) → Stage 1 基准锚定残差 MLP 预测 7 个性能参数 + 256 个稀疏阻抗点 → Stage 2 Transformer 重建 1001 点 Z11(dB) 全谱。

**架构：** 训练数据从桌面版 sqlite + .s2p 文件提取（复用 `nn_export` 查询与 `sparse_sampler` 读谱），两级清洗后按 480 个结构网格单元划分 train/val/test（test 格模拟"未流片设计"）。Stage 1 学"同面积 eg=0/fl=0 基准 + 残差"；Stage 2 复用现有 `SparseReconstructor` 架构从头训练（原 308nm checkpoint 已删除）。规格：`docs/superpowers/specs/2026-07-20-surrogate-model-design.md`（v3）。

**技术栈：** PyTorch（MPS）、scikit-rf、SQLAlchemy（sqlite/PG 通吃）、scikit-learn（B3 基线）、numpy/scipy、uv + pytest。

**约定：**
- 所有 Python 命令在 `backend/` 下以 `uv run` 执行；pytest 路径相对 `backend/`。
- 核心算法常数全部进 `backend/app/config.py` 的 `AlgorithmConfig`（任务 1 定义，后文引用如 `surr_k`）。
- torch 只出现在 `dataset.py` / `model.py` / `infer.py` 与训练脚本；`extract.py` / `baseline.py` / `sparse_sampler.py` 保持 torch-free。
- 提交信息遵循仓库中文 conventional commits 风格。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `backend/app/config.py`（修改） | 新增代理模型算法常数 |
| `backend/app/core/sparse_sampler.py`（修改） | 新增 `deterministic_sample_positions`（确定性稀疏位置规则） |
| `backend/app/ml/surrogate/__init__.py`（创建） | 包标识 + 公开导出 |
| `backend/app/ml/surrogate/baseline.py`（创建） | `BaselineTable`：eg=0/fl=0 基准表构建/按面积查询（dB 域插值）/存取 |
| `backend/app/ml/surrogate/extract.py`（创建） | 提取纯函数：单器件记录构建、两级清洗、网格划分、归一化统计、npz/meta 落地 |
| `backend/app/ml/surrogate/dataset.py`（创建） | `SurrogateDataset`：npz → torch Dataset（含归一化与基准对齐） |
| `backend/app/ml/surrogate/model.py`（创建） | `BaselineResidualMLP`：Stage 1 模型 |
| `backend/app/ml/surrogate/infer.py`（创建） | `SurrogatePredictor`：结构参数 → 全谱 端到端推理 |
| `backend/scripts/extract_surrogate_dataset.py`（创建） | 提取 CLI（多进程） |
| `backend/scripts/train_surrogate_stage1.py`（创建） | Stage 1 训练 |
| `backend/scripts/train_baseline_b3.py`（创建） | B3 轻量基线（ExtraTrees） |
| `backend/scripts/train_stage2_surrogate.py`（创建） | Stage 2 从头训练 |
| `backend/scripts/evaluate_surrogate.py`（创建） | 三层指标 + B0/B2/B3 对照 + 报告图 |
| `backend/scripts/predict_spectrum.py`（创建） | 推理 CLI（--eg --fl --area → 图/CSV） |
| `backend/tests/core/test_deterministic_sampling.py`（创建） | 位置规则单测 |
| `backend/tests/ml/test_surrogate_baseline.py`（创建） | 基准表单测 |
| `backend/tests/ml/test_surrogate_extract.py`（创建） | 清洗/划分/提取夹具测试 |
| `backend/tests/ml/test_surrogate_dataset.py`（创建） | Dataset 单测（合成小 npz） |
| `backend/tests/ml/test_surrogate_model.py`（创建） | 模型形状 + 零残差恒等 |
| `backend/tests/ml/test_surrogate_infer.py`（创建） | 端到端推理契约（随机小模型） |
| `backend/tests/ml/conftest.py`（创建） | 合成小 npz 夹具（全测试共用） |

**数据契约（npz + meta.json，任务 5 产出，后续任务都消费它）：**

npz 数组（M = 清洗后器件数，N=1001，K=256）：

| 数组 | 形状 | 说明 |
|---|---|---|
| `x_struct` | (M,5) float32 | `[eg, fl, ag, area_um2, has_pf]`，ag 当前恒 1.0（v4） |
| `y_params` | (M,7) float32 | `[fs_ghz, fp_ghz, k2eff_pct, qs, qp, qs_bodeq, qp_bodeq]` |
| `params_base` | (M,7) float32 | 同面积基准性能参数（提取时查表写入） |
| `sample_pos` | (M,K) int32 | 确定性采样位置（1001 网格上的索引，升序） |
| `y_sparse` | (M,K) float32 | 采样位置上的真值 z(dB) |
| `sparse_base` | (M,K) float32 | 基准全谱在采样位置上的插值（dB） |
| `full_z` | (M,N) float32 | 完整 Z11(dB) |
| `phase` | (M,N) float32 | 相位通道（预留，训练不用） |
| `group_idx` | (M,) int32 | 物理器件组索引（指向 meta.groups，v4） |

meta.json：`cells`（[{eg, fl, area_um2, split}]，split ∈ train/val/test）、`freq_ghz`（N 列表）、`k`、`n_freq`、归一化统计（`x_mean/x_std/params_mean/params_std/z_mean/z_std`，**只在 train 单元上计算**）、`dropped`（清洗剔除清单及原因）、`config`（阈值等）。

---

## 任务 0：环境准备（worktree + 依赖）

CLAUDE.md 路由要求 ML 工作在 `/Users/jingbozuo/Projects/aln-data-backend-ml`（`feat/backend-ml`）进行；该 worktree 不存在，先创建。

- [ ] **步骤 1：创建 worktree 与分支**

```bash
cd /Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master
git worktree add /Users/jingbozuo/Projects/aln-data-backend-ml -b feat/backend-ml
```

预期：`Preparing worktree (new branch 'feat/backend-ml')`。

- [ ] **步骤 2：同步 Python 环境（含 scikit-learn）**

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-ml/backend
uv sync
uv add scikit-learn
uv run python -c "import torch, skrf, sklearn; print(torch.__version__, torch.backends.mps.is_available(), sklearn.__version__)"
```

预期：torch 版本号 + `True` + sklearn 版本号。
已知风险：若 `uv sync` 在 `zipfile-deflate64` 源码编译处失败（新版 macOS SDK 与内置旧 zlib 冲突，已在主工作区复现），回退方案：删除 worktree，直接在主工作区 `git checkout -b feat/backend-ml` 上开发（主工作区 `.venv` 完整可用），计划其余步骤路径相应替换。

- [ ] **步骤 3：Commit 依赖变更**

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-ml
git add backend/pyproject.toml backend/uv.lock
git commit -m "chore: backend 增加 scikit-learn（代理模型 B3 基线）"
```

---

## 任务 1：AlgorithmConfig 新增代理模型常数

**文件：**
- 修改：`backend/app/config.py`（`AlgorithmConfig` 类内追加）

- [ ] **步骤 1：在 `AlgorithmConfig` 类末尾追加常量**

```python
    # --- 代理模型（surrogate）---
    surr_n_freq: int = 1001  # 统一频点数
    surr_k: int = 256  # 稀疏采样点数
    surr_dense_ratio: float = 0.60  # 谐振密集区配额
    surr_transition_ratio: float = 0.25  # 过渡带配额（远带 = 1 - 前两者）
    surr_dense_half_bw: float = 0.5  # 密集区半宽 = 该值 × (fp - fs)
    surr_transition_half_bw: float = 1.5  # 过渡带外沿 = 该值 × (fp - fs)
    surr_clean_fs_tol_mhz: float = 100.0  # 单元内 fs/fp 离群阈值
    surr_clean_trend_sigma: float = 3.0  # 跨单元趋势面离群阈值（σ 倍数）
    surr_val_cells: int = 48  # val 网格单元数
    surr_test_cells: int = 48  # test 网格单元数
    surr_split_seed: int = 42  # 划分随机种子
    surr_param_cols: tuple = (  # 7 个性能参数列（顺序固定，全流程一致）
        "fs_ghz", "fp_ghz", "k2eff_pct", "qs", "qp", "qs_bodeq", "qp_bodeq",
    )
```

- [ ] **步骤 2：验证可导入**

运行：`uv run python -c "from app.config import AlgorithmConfig as A; print(A.surr_k, A.surr_param_cols)"`
预期：`256 ('fs_ghz', 'fp_ghz', 'k2eff_pct', 'qs', 'qp', 'qs_bodeq', 'qp_bodeq')`

- [ ] **步骤 3：Commit**

```bash
git add backend/app/config.py
git commit -m "feat(config): 代理模型算法常数（采样/清洗/划分/参数列）"
```

---

## 任务 2：确定性稀疏位置规则 `deterministic_sample_positions`

**文件：**
- 修改：`backend/app/core/sparse_sampler.py`（追加函数 + `__all__` 导出）
- 测试：`backend/tests/core/test_deterministic_sampling.py`

规则（规格 §5.3）：位置只依赖 fs/fp 与频率轴。密集区 `[fs−0.5Δ, fp+0.5Δ]`（Δ=fp−fs）配额 60%；过渡带两侧各延伸至 1.5Δ，配额 25%（左右均分，奇数给右侧）；远带其余 15%。各区域内均匀取格点。某区域格点不足配额时，缺口按 密集→过渡→远带 顺序让渡。fs/fp 非法（`fs<=0` 或 `fp<=fs`）退化为全轴均匀。

- [ ] **步骤 1：编写失败的测试**

```python
# backend/tests/core/test_deterministic_sampling.py
"""确定性稀疏位置规则单测。"""

import numpy as np

from app.core.sparse_sampler import deterministic_sample_positions

FREQ = np.linspace(4.0, 7.0, 1001)  # 批次 #2 频段
FS, FP = 4.8, 5.1  # Δ = 0.3


class TestDeterministicSamplePositions:
    def test_determinism(self):
        a = deterministic_sample_positions(FREQ, FS, FP, k=256)
        b = deterministic_sample_positions(FREQ, FS, FP, k=256)
        np.testing.assert_array_equal(a, b)

    def test_count_and_order_and_bounds(self):
        pos = deterministic_sample_positions(FREQ, FS, FP, k=256)
        assert len(pos) == 256
        assert np.all(np.diff(pos) > 0)  # 严格升序、无重复
        assert pos[0] >= 0 and pos[-1] <= 1000

    def test_dense_region_gets_quota(self):
        pos = deterministic_sample_positions(FREQ, FS, FP, k=256)
        f = FREQ[pos]
        delta = FP - FS
        in_dense = (f >= FS - 0.5 * delta) & (f <= FP + 0.5 * delta)
        # 密集区配额 60%（格点充足时精确命中）
        assert in_dense.sum() == round(256 * 0.60)

    def test_densest_near_resonance(self):
        """密集区内的点间距应小于远带。"""
        pos = deterministic_sample_positions(FREQ, FS, FP, k=256)
        f = FREQ[pos]
        delta = FP - FS
        dense_gaps = np.diff(f[(f >= FS - 0.5 * delta) & (f <= FP + 0.5 * delta)])
        far_mask = (f < FS - 1.5 * delta) | (f > FP + 1.5 * delta)
        far_gaps = np.diff(f[far_mask])
        assert dense_gaps.mean() < far_gaps.mean()

    def test_edge_truncation_fs_near_band_edge(self):
        """fs 贴近下边缘时左侧让渡，总数不变、位置合法。"""
        pos = deterministic_sample_positions(FREQ, fs=4.05, fp=4.35, k=256)
        assert len(pos) == 256
        assert pos[0] >= 0 and pos[-1] <= 1000
        assert np.all(np.diff(pos) > 0)

    def test_invalid_fs_fp_falls_back_to_uniform(self):
        pos = deterministic_sample_positions(FREQ, fs=0.0, fp=0.0, k=256)
        expected = np.linspace(0, 1000, 256).round().astype(np.int64)
        np.testing.assert_array_equal(pos, expected)

    def test_k_capped_by_n(self):
        small_axis = np.linspace(4.0, 7.0, 100)
        pos = deterministic_sample_positions(small_axis, FS, FP, k=256)
        assert len(pos) == 100
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/core/test_deterministic_sampling.py -v`
预期：FAIL，`ImportError: cannot import name 'deterministic_sample_positions'`

- [ ] **步骤 3：实现 `deterministic_sample_positions`**

在 `backend/app/core/sparse_sampler.py` 的 `__all__` 中追加 `"deterministic_sample_positions"`，文件头部导入区追加 `from app.config import AlgorithmConfig`，并在文件末尾追加：

```python
def deterministic_sample_positions(
    freq_ghz: NDArray[np.float64],
    fs: float,
    fp: float,
    k: int = AlgorithmConfig.surr_k,
    dense_ratio: float = AlgorithmConfig.surr_dense_ratio,
    transition_ratio: float = AlgorithmConfig.surr_transition_ratio,
    dense_half_bw: float = AlgorithmConfig.surr_dense_half_bw,
    transition_half_bw: float = AlgorithmConfig.surr_transition_half_bw,
) -> NDArray[np.int64]:
    """确定性稀疏采样位置（规格 §5.3）。

    只依赖 fs/fp 与频率轴，不看频谱内容，保证训练/推理一致。
    返回 freq_ghz 网格上的升序索引。

    区域划分（Δ = fp - fs）：
        密集区  [fs-0.5Δ, fp+0.5Δ]   配额 k×dense_ratio
        过渡带  两侧各延伸至 ±1.5Δ    配额 k×transition_ratio（两带拼接后整体均匀取样）
        远带    其余                  剩余配额
    区域格点不足配额时，缺口按 密集→过渡→远带 顺序让渡。
    fs/fp 非法（fs<=0 或 fp<=fs）退化为全轴均匀取点。
    """
    n = len(freq_ghz)
    k = min(k, n)
    if fs <= 0 or fp <= fs:
        return np.linspace(0, n - 1, k).round().astype(np.int64)

    delta = fp - fs
    fmin, fmax = float(freq_ghz[0]), float(freq_ghz[-1])

    dense_lo = max(fs - dense_half_bw * delta, fmin)
    dense_hi = min(fp + dense_half_bw * delta, fmax)
    trans_lo = max(fs - transition_half_bw * delta, fmin)
    trans_hi = min(fp + transition_half_bw * delta, fmax)

    def _idx(lo: float, hi: float) -> NDArray[np.int64]:
        return np.where((freq_ghz >= lo) & (freq_ghz <= hi))[0]

    dense_idx = _idx(dense_lo, dense_hi)
    trans_idx = np.concatenate(
        [_idx(trans_lo, dense_lo - 1e-12), _idx(dense_hi + 1e-12, trans_hi)]
    )
    occupied = np.zeros(n, dtype=bool)
    occupied[dense_idx] = True
    occupied[trans_idx] = True
    far_idx = np.where(~occupied)[0]

    def _take(idx: NDArray[np.int64], m: int) -> NDArray[np.int64]:
        """在 idx 内均匀取 m 个（m <= len(idx)）。"""
        if m <= 0 or len(idx) == 0:
            return np.array([], dtype=np.int64)
        sel = np.linspace(0, len(idx) - 1, min(m, len(idx))).round().astype(np.int64)
        return idx[sel]

    # 初始配额
    q_dense = int(round(k * dense_ratio))
    q_trans = int(round(k * transition_ratio))
    q_far = k - q_dense - q_trans

    take_dense = min(q_dense, len(dense_idx))
    take_trans = min(q_trans, len(trans_idx))
    take_far = min(q_far, len(far_idx))

    # 让渡：缺口按 密集→过渡→远带 顺序补给有余量的区域
    leftover = k - (take_dense + take_trans + take_far)
    caps = [
        ("dense", len(dense_idx) - take_dense),
        ("trans", len(trans_idx) - take_trans),
        ("far", len(far_idx) - take_far),
    ]
    while leftover > 0 and any(c > 0 for _, c in caps):
        for i, (name, cap) in enumerate(caps):
            if leftover <= 0:
                break
            if cap <= 0:
                continue
            if name == "dense":
                take_dense += 1
            elif name == "trans":
                take_trans += 1
            else:
                take_far += 1
            caps[i] = (name, cap - 1)
            leftover -= 1

    result = np.concatenate(
        [
            _take(dense_idx, take_dense),
            _take(trans_idx, take_trans),
            _take(far_idx, take_far),
        ]
    )
    return np.sort(result)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/core/test_deterministic_sampling.py -v`
预期：7 passed

- [ ] **步骤 5：回归确认不破坏现有导出**

运行：`uv run pytest tests/core/test_sparse_sampler.py tests/api/test_nn_export.py -q`
预期：全部 passed（本任务只做新增，不改既有函数）

- [ ] **步骤 6：Commit**

```bash
git add backend/app/core/sparse_sampler.py backend/tests/core/test_deterministic_sampling.py
git commit -m "feat(core): 确定性稀疏位置规则（只看 fs/fp，训练/推理一致）"
```

---

## 任务 3：基准表 `BaselineTable`

**文件：**
- 创建：`backend/app/ml/surrogate/__init__.py`
- 创建：`backend/app/ml/surrogate/baseline.py`
- 测试：`backend/tests/ml/test_surrogate_baseline.py`

职责：对 12 个 `(eg=0, fl=0, area)` 基准单元的清洗后记录求均值，得到每面积档的 `params_base(7)` 与 `z_base_full(N)`；查询时面积不在档位上则相邻档 dB 域线性插值。

- [ ] **步骤 1：创建包文件**

```python
# backend/app/ml/surrogate/__init__.py
"""谐振器代理模型（surrogate model）：结构参数 → 性能参数 + 阻抗谱。"""

from app.ml.surrogate.baseline import BaselineTable

__all__ = ["BaselineTable"]
```

- [ ] **步骤 2：编写失败的测试**

```python
# backend/tests/ml/test_surrogate_baseline.py
"""基准表构建/查询/插值/存取单测。"""

import numpy as np

from app.ml.surrogate.baseline import BaselineTable


def _record(eg, fl, area, params, z_full):
    return {
        "eg": eg, "fl": fl, "ag": 0.0, "area_um2": area,
        "params": np.asarray(params, dtype=np.float64),
        "full_z": np.asarray(z_full, dtype=np.float64),
    }


def _make_records():
    """3 个面积档 × 基准(eg=0,fl=0) 各 2 条 + 非基准 1 条。"""
    recs = []
    for area in (700, 1500, 3000):
        for i in range(2):
            recs.append(_record(
                0.0, 0.0, area,
                params=[4.8, 5.1, 7.0, 500 + i * 10, 400, 450, 380],
                z_full=np.full(101, 30.0 + area / 1000 + i),
            ))
    recs.append(_record(
        1.0, 0.5, 1500,
        params=[4.9, 5.2, 7.2, 600, 500, 550, 480],
        z_full=np.full(101, 50.0),
    ))
    return recs


class TestBaselineTable:
    def test_build_uses_only_baseline_cells(self):
        table = BaselineTable.build(_make_records())
        assert table.areas.tolist() == [700, 1500, 3000]
        # params_base 是基准格两条记录的均值（qs = (510+520)/2 = 515）
        row = table.params_base[1]
        assert row[3] == 515.0
        # z_base_full 均值（area=1500 → 30+1.5+i → mean = 32.0）
        np.testing.assert_allclose(table.z_base_full[1], 32.0)

    def test_lookup_exact_area(self):
        table = BaselineTable.build(_make_records())
        params, z = table.lookup(700)
        np.testing.assert_allclose(params, table.params_base[0])
        np.testing.assert_allclose(z, table.z_base_full[0])

    def test_lookup_interpolates_in_db_domain(self):
        table = BaselineTable.build(_make_records())
        params, z = table.lookup(1100)  # 700 与 1500 之间
        w = (1100 - 700) / (1500 - 700)
        expected_z = (1 - w) * table.z_base_full[0] + w * table.z_base_full[1]
        expected_p = (1 - w) * table.params_base[0] + w * table.params_base[1]
        np.testing.assert_allclose(z, expected_z)
        np.testing.assert_allclose(params, expected_p)

    def test_lookup_out_of_range_clamps(self):
        table = BaselineTable.build(_make_records())
        params, z = table.lookup(100)  # 小于最小档 → 取最小档
        np.testing.assert_allclose(z, table.z_base_full[0])
        params, z = table.lookup(9999)
        np.testing.assert_allclose(z, table.z_base_full[-1])

    def test_save_load_roundtrip(self, tmp_path):
        table = BaselineTable.build(_make_records())
        path = tmp_path / "baseline.npz"
        table.save(path)
        loaded = BaselineTable.load(path)
        np.testing.assert_allclose(loaded.areas, table.areas)
        np.testing.assert_allclose(loaded.params_base, table.params_base)
        np.testing.assert_allclose(loaded.z_base_full, table.z_base_full)

    def test_build_without_baseline_records_raises(self):
        import pytest
        with pytest.raises(ValueError, match="基准单元"):
            BaselineTable.build([_record(1.0, 1.0, 700, [4.8]*7, [30.0]*101)])
```

- [ ] **步骤 3：运行测试验证失败**

运行：`uv run pytest tests/ml/test_surrogate_baseline.py -v`
预期：FAIL，`ModuleNotFoundError: No module named 'app.ml.surrogate'`

- [ ] **步骤 4：实现 `BaselineTable`**

```python
# backend/app/ml/surrogate/baseline.py
"""eg=0/fl=0 基准表：每面积档的均值性能参数与均值全谱（规格 §5.4）。

基准表只在清洗后的基准单元记录上构建；查询时面积不在档位上，
对相邻面积档做 dB 域线性插值（z 已是 dB，params 直接线性）。
torch-free。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from numpy.typing import NDArray


class BaselineTable:
    """按面积档组织的基准表。

    Attributes:
        areas: (A,) 升序面积档（μm²）。
        params_base: (A, 7) 每档基准性能参数均值。
        z_base_full: (A, N) 每档基准全谱均值（dB）。
    """

    def __init__(
        self,
        areas: NDArray[np.float64],
        params_base: NDArray[np.float64],
        z_base_full: NDArray[np.float64],
    ) -> None:
        self.areas = np.asarray(areas, dtype=np.float64)
        self.params_base = np.asarray(params_base, dtype=np.float64)
        self.z_base_full = np.asarray(z_base_full, dtype=np.float64)
        if not (len(self.areas) == len(self.params_base) == len(self.z_base_full)):
            raise ValueError("areas/params_base/z_base_full 行数不一致")

    @classmethod
    def build(cls, records: list[dict]) -> BaselineTable:
        """从清洗后的记录构建（只用 eg=0 且 fl=0 的基准单元）。"""
        base = [r for r in records if r["eg"] == 0.0 and r["fl"] == 0.0]
        if not base:
            raise ValueError("没有 eg=0/fl=0 的基准单元记录，无法构建基准表")
        areas = sorted({r["area_um2"] for r in base})
        params_rows, z_rows = [], []
        for area in areas:
            group = [r for r in base if r["area_um2"] == area]
            params_rows.append(np.mean([r["params"] for r in group], axis=0))
            z_rows.append(np.mean([r["full_z"] for r in group], axis=0))
        return cls(
            np.asarray(areas, dtype=np.float64),
            np.stack(params_rows),
            np.stack(z_rows),
        )

    def lookup(self, area: float) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """按面积查询 (params_base(7,), z_base_full(N,))；档间线性插值、越界取端点。"""
        a = float(area)
        if a <= self.areas[0]:
            return self.params_base[0], self.z_base_full[0]
        if a >= self.areas[-1]:
            return self.params_base[-1], self.z_base_full[-1]
        hi = int(np.searchsorted(self.areas, a))
        lo = hi - 1
        w = (a - self.areas[lo]) / (self.areas[hi] - self.areas[lo])
        params = (1 - w) * self.params_base[lo] + w * self.params_base[hi]
        z = (1 - w) * self.z_base_full[lo] + w * self.z_base_full[hi]
        return params, z

    def save(self, path: str | Path) -> None:
        np.savez_compressed(
            path, areas=self.areas, params_base=self.params_base, z_base_full=self.z_base_full
        )

    @classmethod
    def load(cls, path: str | Path) -> BaselineTable:
        data = np.load(path)
        return cls(data["areas"], data["params_base"], data["z_base_full"])
```

- [ ] **步骤 5：运行测试验证通过**

运行：`uv run pytest tests/ml/test_surrogate_baseline.py -v`
预期：6 passed

- [ ] **步骤 6：Commit**

```bash
git add backend/app/ml/surrogate/__init__.py backend/app/ml/surrogate/baseline.py backend/tests/ml/test_surrogate_baseline.py
git commit -m "feat(ml): 代理模型基准表（eg=0/fl=0 均值 + 面积插值查询）"
```

---

## 任务 3.5：基准表加 PF 维 + AlgorithmConfig 新常量 + 质量修复收尾（v4 设计变更）

**文件：**
- 修改：`backend/app/config.py`（AlgorithmConfig 追加 4 个常量）
- 修改：`backend/app/ml/surrogate/baseline.py`（加 PF 维 + 两个质量修复）
- 测试：`backend/tests/ml/test_surrogate_baseline.py`

背景：v4 规格把输入改为 5 维（EG, FL, AG=1.0 占位, 面积, PF）。PF 来自 `mapping_entries.has_pf`（0/1）。基准表从"按面积 12 档"变为"按 (pf, area) 24 档"。任务 3 的基准表已实现一维版本，本任务升级为二维，并顺带完成任务 3 质量审查遗留的两个小修复。

- [ ] **步骤 1：AlgorithmConfig 追加常量**

```python
    surr_per_cell_max: int = 5  # 每结构组合抽样上限（|fs-中位数| 升序取前 N）
    surr_default_ag: float = 1.0  # AG 占位默认值（μm；数据全空时的常量输入）
    surr_val_groups: int = 29  # val 物理器件组数
    surr_test_groups: int = 29  # test 物理器件组数
```

- [ ] **步骤 2：改测试（TDD——先改测试确认失败）**

测试 fixture 的记录 dict 加 `"has_pf"` 键；`BaselineTable.build` 按 (has_pf, area) 分组；`lookup(area, pf)` 按 pf 选层、面积插值：

```python
# backend/tests/ml/test_surrogate_baseline.py（全文替换）
"""基准表构建/查询/插值/存取单测（v4：PF 维）。"""

import numpy as np
import pytest

from app.ml.surrogate.baseline import BaselineTable


def _record(eg, fl, area, params, z_full, has_pf=0):
    return {
        "eg": eg, "fl": fl, "ag": 0.0, "area_um2": area, "has_pf": has_pf,
        "params": np.asarray(params, dtype=np.float64),
        "full_z": np.asarray(z_full, dtype=np.float64),
    }


def _make_records():
    """2 个面积档 × 2 个 PF × 基准各 2 条 + 非基准 2 条。"""
    recs = []
    for pf in (0, 1):
        for area in (700, 1500):
            for i in range(2):
                recs.append(_record(
                    0.0, 0.0, area,
                    params=[4.8, 5.1, 7.0, 510 + i * 10 + pf * 100, 400, 450, 380],
                    z_full=np.full(101, 30.0 + area / 1000 + i + pf * 10),
                    has_pf=pf,
                ))
    # 非基准：eg≠0、以及 eg=0 但 fl≠0（也不应进基准表）
    recs.append(_record(1.0, 0.5, 1500, [4.9, 5.2, 7.2, 600, 500, 550, 480],
                        np.full(101, 50.0)))
    recs.append(_record(0.0, 1.0, 1500, [4.9, 5.2, 7.2, 600, 500, 550, 480],
                        np.full(101, 50.0)))
    return recs


class TestBaselineTable:
    def test_build_groups_by_pf_and_area(self):
        table = BaselineTable.build(_make_records())
        assert table.areas.tolist() == [700, 1500]
        assert table.pfs.tolist() == [0.0, 1.0]
        # pf=0, area=1500: qs 均值 = (510+520)/2 = 515
        assert table.params_base[0, 1, 3] == 515.0
        # pf=1, area=1500: qs 均值 = (610+620)/2 = 615
        assert table.params_base[1, 1, 3] == 615.0
        # z_base_full pf=1, area=700: 30+0.7+10+i → mean 41.2
        np.testing.assert_allclose(table.z_base_full[1, 0], 41.2)

    def test_lookup_exact(self):
        table = BaselineTable.build(_make_records())
        params, z = table.lookup(700, 0)
        np.testing.assert_allclose(params, table.params_base[0, 0])
        np.testing.assert_allclose(z, table.z_base_full[0, 0])
        _, z1 = table.lookup(700, 1)
        np.testing.assert_allclose(z1, table.z_base_full[1, 0])

    def test_lookup_interpolates_area_in_db_domain(self):
        table = BaselineTable.build(_make_records())
        params, z = table.lookup(900, 0)  # 700/1500 之间，w=0.25
        w = (900 - 700) / (1500 - 700)
        np.testing.assert_allclose(z, (1 - w) * table.z_base_full[0, 0] + w * table.z_base_full[0, 1])
        np.testing.assert_allclose(
            params, (1 - w) * table.params_base[0, 0] + w * table.params_base[0, 1]
        )

    def test_lookup_out_of_range_clamps(self):
        table = BaselineTable.build(_make_records())
        _, z = table.lookup(100, 0)
        np.testing.assert_allclose(z, table.z_base_full[0, 0])
        _, z = table.lookup(9999, 1)
        np.testing.assert_allclose(z, table.z_base_full[1, -1])

    def test_lookup_returns_copies(self):
        """越界 clamp 分支返回副本，原地修改不污染表。"""
        table = BaselineTable.build(_make_records())
        _, z = table.lookup(100, 0)
        z[:] = -999
        assert table.z_base_full[0, 0, 0] != -999

    def test_save_load_roundtrip(self, tmp_path):
        table = BaselineTable.build(_make_records())
        path = tmp_path / "baseline.npz"
        table.save(path)
        loaded = BaselineTable.load(path)
        np.testing.assert_allclose(loaded.areas, table.areas)
        np.testing.assert_allclose(loaded.pfs, table.pfs)
        np.testing.assert_allclose(loaded.params_base, table.params_base)
        np.testing.assert_allclose(loaded.z_base_full, table.z_base_full)

    def test_build_without_baseline_records_raises(self):
        with pytest.raises(ValueError, match="基准单元"):
            BaselineTable.build([_record(1.0, 1.0, 700, [4.8] * 7, [30.0] * 101)])
```

- [ ] **步骤 3：运行测试验证失败**

运行（工作目录 `backend/`）：`uv run pytest tests/ml/test_surrogate_baseline.py -v`
预期：FAIL（`AttributeError: pfs` / `TypeError: lookup() takes 2 positional arguments but 3 were given`）

- [ ] **步骤 4：实现 PF 维 BaselineTable（含两个质量修复）**

```python
# backend/app/ml/surrogate/baseline.py（全文替换）
"""eg=0/fl=0 基准表：按 (pf, area) 组织的均值性能参数与均值全谱（规格 §5.4 v4）。

基准表只在清洗后的基准单元记录上构建；查询时 pf 取精确档（0/1），
面积不在档位上时对相邻面积档做 dB 域线性插值（z 已是 dB，params 直接线性）。
torch-free。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from numpy.typing import NDArray


class BaselineTable:
    """按 (pf, area) 二维组织的基准表。

    Attributes:
        pfs: (P,) 升序 pf 档（[0., 1.]）。
        areas: (A,) 升序面积档（μm²）。
        params_base: (P, A, 7) 每档基准性能参数均值。
        z_base_full: (P, A, N) 每档基准全谱均值（dB）。
    """

    def __init__(
        self,
        pfs: NDArray[np.float64],
        areas: NDArray[np.float64],
        params_base: NDArray[np.float64],
        z_base_full: NDArray[np.float64],
    ) -> None:
        self.pfs = np.asarray(pfs, dtype=np.float64)
        self.areas = np.asarray(areas, dtype=np.float64)
        self.params_base = np.asarray(params_base, dtype=np.float64)
        self.z_base_full = np.asarray(z_base_full, dtype=np.float64)
        if not np.all(np.diff(self.areas) > 0):
            raise ValueError("areas 必须严格递增")
        expected = (len(self.pfs), len(self.areas))
        if self.params_base.shape[:2] != expected or self.z_base_full.shape[:2] != expected:
            raise ValueError("pfs/areas 与 params_base/z_base_full 形状不一致")

    @classmethod
    def build(cls, records: list[dict]) -> BaselineTable:
        """从清洗后的记录构建（只用 eg=0 且 fl=0 的基准单元，按 (has_pf, area) 分组）。"""
        base = [r for r in records if r["eg"] == 0.0 and r["fl"] == 0.0]
        if not base:
            raise ValueError("没有 eg=0/fl=0 的基准单元记录，无法构建基准表")
        groups: dict[tuple[int, int], list[dict]] = {}
        for r in base:
            groups.setdefault((int(r["has_pf"]), r["area_um2"]), []).append(r)
        pfs = sorted({k[0] for k in groups})
        areas = sorted({k[1] for k in groups})
        params = np.zeros((len(pfs), len(areas), 7))
        z = np.zeros((len(pfs), len(areas), len(base[0]["full_z"])))
        for (pf, area), group in groups.items():
            pi, ai = pfs.index(pf), areas.index(area)
            params[pi, ai] = np.mean([r["params"] for r in group], axis=0)
            z[pi, ai] = np.mean([r["full_z"] for r in group], axis=0)
        return cls(
            np.asarray(pfs, dtype=np.float64),
            np.asarray(areas, dtype=np.float64),
            params,
            z,
        )

    def lookup(self, area: float, pf: float) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """按 (面积, pf) 查询 (params_base(7,), z_base_full(N,))。

        pf 取最近档（0/1 精确语义）；面积档间线性插值、越界取端点（返回副本）。
        """
        pi = int(np.argmin(np.abs(self.pfs - float(pf))))
        a = float(area)
        if a <= self.areas[0]:
            return self.params_base[pi, 0].copy(), self.z_base_full[pi, 0].copy()
        if a >= self.areas[-1]:
            return self.params_base[pi, -1].copy(), self.z_base_full[pi, -1].copy()
        hi = int(np.searchsorted(self.areas, a))
        lo = hi - 1
        w = (a - self.areas[lo]) / (self.areas[hi] - self.areas[lo])
        params = (1 - w) * self.params_base[pi, lo] + w * self.params_base[pi, hi]
        z = (1 - w) * self.z_base_full[pi, lo] + w * self.z_base_full[pi, hi]
        return params, z

    def save(self, path: str | Path) -> None:
        np.savez_compressed(
            path, pfs=self.pfs, areas=self.areas,
            params_base=self.params_base, z_base_full=self.z_base_full,
        )

    @classmethod
    def load(cls, path: str | Path) -> BaselineTable:
        with np.load(path) as data:
            return cls(data["pfs"], data["areas"], data["params_base"], data["z_base_full"])
```

- [ ] **步骤 5：运行测试验证通过**

运行：`uv run pytest tests/ml/test_surrogate_baseline.py -v`
预期：7 passed

- [ ] **步骤 6：ruff + Commit**

```bash
uv run ruff check backend/app/ml/surrogate/ backend/tests/ml/
git add backend/app/config.py backend/app/ml/surrogate/baseline.py backend/tests/ml/test_surrogate_baseline.py
git commit -m "feat(ml): 基准表加 PF 维（lookup(area, pf)）+ 抽样/划分常量"
```

---

## 任务 4：提取纯函数模块 `surrogate/extract.py`（v4）

**文件：**
- 创建：`backend/app/ml/surrogate/extract.py`
- 测试：`backend/tests/ml/test_surrogate_extract.py`

职责（全部 torch-free 纯函数，脚本只负责编排）：
1. `build_device_record(device_row, files_dir, freq_axis, k)`：单器件 → 记录 dict（读谱 → 确定性位置 → 采样 → 记录）
2. `clean_records(records, ...)`：两级清洗（规格 §5.2）
3. `select_per_cell(records, max_per_cell)`：每结构组合按 |fs−格中位数| 升序取前 N（v4，规格 §5.1）
4. `split_groups(groups, ...)`：物理器件组（eg, fl, pf, mark）划分，基准组固定 train（v4，规格 §5.5）
5. `compute_norm_stats(records, splits)`：归一化统计（只用 train 组，防泄漏）

记录 dict 结构（v4）：

```python
{
    "device_id": int, "eg": float, "fl": float, "ag": float,  # ag 当前 = surr_default_ag
    "area_um2": int, "has_pf": int,  # 0/1，由脚本 JOIN mapping_entries 提供
    "mark": str,  # 物理结构标识（划分组键；同 mark 的 S11/S22 两行与重复管芯同组）
    "cell_key": str,  # f"{eg}|{fl}|{area}|{pf}"（抽样单元）
    "params": (7,), "sample_pos": (K,), "sample_z": (K,), "sparse_base": (K,),
    "full_z": (N,), "phase": (N,),
}
```

- [ ] **步骤 1：编写失败的测试（清洗 + 抽样 + 划分 + 统计；读谱在任务 5 夹具里测）**

```python
# backend/tests/ml/test_surrogate_extract.py
"""提取管线纯函数单测：两级清洗、每格抽样、物理组划分、归一化统计（v4）。"""

import numpy as np
import pytest

from app.ml.surrogate.extract import (
    clean_records,
    compute_norm_stats,
    select_per_cell,
    split_groups,
)

N = 101


def _rec(eg, fl, area, pf=0, mark="M1", fs=4.8, fp=5.1, qs=500.0, qp=400.0):
    return {
        "device_id": 1, "eg": eg, "fl": fl, "ag": 1.0, "area_um2": area,
        "has_pf": pf, "mark": mark,
        "cell_key": f"{eg}|{fl}|{area}|{pf}",
        "params": np.array([fs, fp, 7.0, qs, qp, 450.0, 380.0]),
        "sample_pos": np.arange(32), "sample_z": np.full(32, 40.0),
        "sparse_base": np.full(32, 39.0),
        "full_z": np.full(N, 40.0), "phase": np.zeros(N),
    }


class TestCleanRecords:
    def test_physical_illegal_dropped(self):
        recs = [_rec(0.0, 0.0, 700), _rec(1.0, 0.5, 700, qs=0.0), _rec(1.0, 0.5, 700, fp=4.0)]
        kept, dropped = clean_records(recs, fs_tol_mhz=1e9, trend_sigma=1e9)
        assert len(kept) == 1
        assert len(dropped) == 2
        assert all(d["reason"] == "physical" for d in dropped)

    def test_cell_outlier_dropped(self):
        """单元内 fs 偏离中位数超阈值 → 剔除。"""
        recs = [_rec(1.0, 0.5, 700, fs=4.80), _rec(1.0, 0.5, 700, fs=4.81),
                _rec(1.0, 0.5, 700, fs=4.79), _rec(1.0, 0.5, 700, fs=5.50)]
        kept, dropped = clean_records(recs, fs_tol_mhz=100.0, trend_sigma=1e9)
        assert len(kept) == 3
        assert len(dropped) == 1
        assert dropped[0]["reason"] == "cell_outlier"

    def test_trend_outlier_dropped(self):
        """跨单元趋势：同一面积档某格 fs 中位数偏离趋势面 >3σ → 整格剔除。"""
        rng = np.random.default_rng(0)
        recs = []
        # fs 随 eg 线性变化：fs = 4.7 + 0.1*eg；每格 4 条加微小抖动
        for eg in (0.0, 0.5, 1.0, 1.5, 2.0):
            for fl in (0.0, 0.5, 1.0):
                fs = 4.7 + 0.1 * eg
                if eg == 1.0 and fl == 0.5:
                    fs = 5.5  # 异常格
                for i in range(4):
                    recs.append(_rec(eg, fl, 700, mark=f"M{eg}{fl}", fs=fs + rng.normal(0, 0.001)))
        kept, dropped = clean_records(recs, fs_tol_mhz=100.0, trend_sigma=3.0)
        dropped_cells = {d["cell_key"] for d in dropped if d["reason"] == "trend_outlier"}
        assert "1.0|0.5|700|0" in dropped_cells
        assert len(kept) == 60 - 4

    def test_pf_separates_cells(self):
        """同 eg/fl/area 不同 pf 是两个独立抽样单元，各自做中位数过滤。"""
        recs = [_rec(1.0, 0.5, 700, pf=0, fs=4.80), _rec(1.0, 0.5, 700, pf=0, fs=4.81),
                _rec(1.0, 0.5, 700, pf=1, mark="H1", fs=4.60), _rec(1.0, 0.5, 700, pf=1, mark="H1", fs=4.61)]
        kept, dropped = clean_records(recs, fs_tol_mhz=100.0, trend_sigma=1e9)
        assert len(kept) == 4  # pf=1 的 fs 低 0.2GHz 但不与 pf=0 混算中位数


class TestSelectPerCell:
    def test_caps_at_max_and_prefers_median(self):
        """8 条同格记录 → 取 |fs-中位数| 最小的 5 条。"""
        fss = [4.80, 4.81, 4.79, 4.82, 4.78, 4.90, 4.70, 4.85]
        recs = [_rec(1.0, 0.5, 700, fs=f) for f in fss]
        for i, r in enumerate(recs):
            r["device_id"] = i
        selected = select_per_cell(recs, max_per_cell=5)
        assert len(selected) == 5
        median = float(np.median(fss))  # 4.805
        kept_fs = sorted(r["params"][0] for r in selected)
        # 离中位数最近的 5 个：4.80, 4.81, 4.79, 4.82, 4.78（4.85/4.90/4.70 落选）
        assert kept_fs == [4.78, 4.79, 4.80, 4.81, 4.82]

    def test_fewer_than_max_keeps_all(self):
        recs = [_rec(1.0, 0.5, 700, fs=4.8), _rec(1.0, 0.5, 700, fs=4.9)]
        assert len(select_per_cell(recs, max_per_cell=5)) == 2

    def test_pf_cells_selected_independently(self):
        recs = [_rec(1.0, 0.5, 700, pf=0, fs=4.8 + i * 0.01) for i in range(7)]
        recs += [_rec(1.0, 0.5, 700, pf=1, mark="H1", fs=4.6 + i * 0.01) for i in range(3)]
        selected = select_per_cell(recs, max_per_cell=5)
        assert len(selected) == 5 + 3


class TestSplitGroups:
    def _groups(self):
        """8 个物理组：2 个基准（eg=0,fl=0）+ 6 个非基准。"""
        groups = [(0.0, 0.0, 0, "A1-1"), (0.0, 0.0, 1, "G1-1")]
        groups += [(1.0, 0.5, 0, f"B{i}") for i in range(4)]
        groups += [(1.0, 0.5, 1, f"H{i}") for i in range(2)]
        return groups

    def test_baseline_groups_stay_in_train(self):
        splits = split_groups(self._groups(), val_n=2, test_n=2, seed=42)
        assert splits[(0.0, 0.0, 0, "A1-1")] == "train"
        assert splits[(0.0, 0.0, 1, "G1-1")] == "train"
        assert sum(v == "val" for v in splits.values()) == 2
        assert sum(v == "test" for v in splits.values()) == 2

    def test_deterministic_with_seed(self):
        s1 = split_groups(self._groups(), val_n=2, test_n=2, seed=42)
        s2 = split_groups(self._groups(), val_n=2, test_n=2, seed=42)
        assert s1 == s2


class TestComputeNormStats:
    def test_stats_only_from_train_groups(self):
        recs = [_rec(0.0, 0.0, 700, mark="A1-1"), _rec(1.0, 0.5, 700, mark="B0")]
        splits = {(0.0, 0.0, 0, "A1-1"): "train", (1.0, 0.5, 0, "B0"): "test"}
        stats = compute_norm_stats(recs, splits)
        # 只有 train 记录（eg=0）参与：x_mean[0] == 0
        assert stats["x_mean"][0] == pytest.approx(0.0)
        assert stats["x_mean"][4] == pytest.approx(0.0)  # has_pf 列
        assert set(stats.keys()) == {"x_mean", "x_std", "params_mean", "params_std", "z_mean", "z_std"}
```

- [ ] **步骤 2：运行测试验证失败**

运行（工作目录 `backend/`）：`uv run pytest tests/ml/test_surrogate_extract.py -v`
预期：FAIL，`ImportError: cannot import name 'clean_records'`

- [ ] **步骤 3：实现 `extract.py`**

```python
# backend/app/ml/surrogate/extract.py
"""代理模型训练数据提取纯函数（规格 §5，v4）。torch-free。

记录 dict 结构见计划"数据契约"；has_pf/mark 由脚本经 mapping JOIN 提供。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from app.config import AlgorithmConfig as AC
from app.core.sparse_sampler import deterministic_sample_positions, read_z_mag_db

def build_device_record(
    device_row: dict,
    files_dir: str | Path,
    freq_axis: NDArray[np.float64],
    k: int = AlgorithmConfig.surr_k,
) -> dict | None:
    """单器件 → 记录 dict；文件缺失/读取失败返回 None。

    Args:
        device_row: 含 eg/fl/area_um2/has_pf/mark/7 性能参数/s_param_path/s_param_port/batch_no 的行。
        files_dir: DATA_ROOT/files 目录。
        freq_axis: 统一频率轴 (N,)。
        k: 稀疏采样点数。
    """
    rel = device_row.get("s_param_path")
    if not rel:
        return None
    path = Path(files_dir) / str(device_row.get("batch_no")) / str(rel)
    if not path.exists():
        return None
    try:
        freq, z_db = read_z_mag_db(path, port=device_row.get("s_param_port") or "S11",
                                   target_n_freq=len(freq_axis))
        # 相位通道（预留）
        import skrf as rf
        net = rf.Network(str(path))
        port_idx = 1 if (device_row.get("s_param_port") == "S22" and net.nports > 1) else 0
        phase = np.angle(net.z[:, port_idx, port_idx])
        if len(phase) != len(freq_axis):
            phase = np.interp(freq_axis, net.f / 1e9, phase)
    except Exception:
        return None

    fs = float(device_row.get("fs_ghz") or 0.0)
    fp = float(device_row.get("fp_ghz") or 0.0)
    pos = deterministic_sample_positions(freq_axis, fs, fp, k=k)
    params = np.array([float(device_row.get(c) or 0.0) for c in AlgorithmConfig.surr_param_cols])
    eg = float(device_row.get("eg") or 0.0)
    fl = float(device_row.get("fl") or 0.0)
    area = int(device_row.get("area_um2") or 0)
    pf = int(device_row.get("has_pf") or 0)
    return {
        "device_id": int(device_row.get("id") or 0),
        "eg": eg, "fl": fl, "ag": AlgorithmConfig.surr_default_ag, "area_um2": area,
        "has_pf": pf, "mark": str(device_row.get("mark") or ""),
        "cell_key": f"{eg}|{fl}|{area}|{pf}",
        "params": params,
        "sample_pos": pos.astype(np.int32),
        "sample_z": z_db[pos].astype(np.float32),
        "sparse_base": np.zeros(k, dtype=np.float32),  # 基准表构建后回填
        "full_z": z_db.astype(np.float32),
        "phase": phase.astype(np.float32),
    }


def clean_records(
    records: list[dict],
    fs_tol_mhz: float = AlgorithmConfig.surr_clean_fs_tol_mhz,
    trend_sigma: float = AlgorithmConfig.surr_clean_trend_sigma,
) -> tuple[list[dict], list[dict]]:
    """两级清洗（规格 §5.2）。返回 (kept, dropped)，dropped 元素含 reason 字段。

    判定顺序：单元内 fs/fp 离群 → 跨单元趋势面 → 物理非法（qs/qp<=0 或 fp<=fs）。
    物理非法放最后：同时违反多项的记录按更具体的原因（cell_outlier/trend_outlier）
    归类；物理非法记录仍参与单元中位数与趋势面拟合（中位数/最小二乘对其鲁棒）。
    """
    dropped: list[dict] = []

    # 第 1 级：单元内 fs/fp 中位数 ± tol（单元 = eg|fl|area|pf）
    by_cell: dict[str, list[dict]] = {}
    for r in records:
        by_cell.setdefault(r["cell_key"], []).append(r)
    tol_ghz = fs_tol_mhz / 1000.0
    in_cell: list[dict] = []
    for group in by_cell.values():
        fs_med = float(np.median([g["params"][0] for g in group]))
        fp_med = float(np.median([g["params"][1] for g in group]))
        for g in group:
            if abs(g["params"][0] - fs_med) > tol_ghz or abs(g["params"][1] - fp_med) > tol_ghz:
                dropped.append({**g, "reason": "cell_outlier"})
            else:
                in_cell.append(g)

    # 第 2 级：跨单元趋势面（每 (area, pf)：fs/fp 格中位数 ~ (1, eg, fl) 最小二乘）
    by_area_pf: dict[tuple[int, int], dict[str, list[dict]]] = {}
    for r in in_cell:
        by_area_pf.setdefault((r["area_um2"], r["has_pf"]), {}).setdefault(
            r["cell_key"], []).append(r)
    in_trend: list[dict] = []
    for (_area, _pf), cells in by_area_pf.items():
        keys = sorted(cells)
        if len(keys) < 4:  # 格太少无法拟合趋势面，全部保留
            for key in keys:
                in_trend.extend(cells[key])
            continue
        design = np.array([[1.0, *map(float, key.split("|")[:2])] for key in keys])
        bad: set[str] = set()  # fs/fp 任一维超阈值即整格剔除（并集，判定一次）
        for param_idx in (0, 1):  # fs, fp
            med = np.array([
                float(np.median([g["params"][param_idx] for g in cells[key]]))
                for key in keys
            ])
            coef, *_ = np.linalg.lstsq(design, med, rcond=None)
            resid = med - design @ coef
            sigma = max(float(resid.std()), 1e-12)  # 下限防常量数据数值误杀
            bad |= {keys[i] for i in range(len(keys)) if abs(resid[i]) > trend_sigma * sigma}
        for key in keys:
            if key in bad:
                dropped.extend({**g, "reason": "trend_outlier"} for g in cells[key])
            else:
                in_trend.extend(cells[key])

    # 第 0 级（最后判定）：物理非法
    kept: list[dict] = []
    for r in in_trend:
        fs, fp, qs, qp = r["params"][0], r["params"][1], r["params"][3], r["params"][4]
        if qs <= 0 or qp <= 0 or fp <= fs:
            dropped.append({**r, "reason": "physical"})
        else:
            kept.append(r)
    return kept, dropped


def select_per_cell(
    records: list[dict],
    max_per_cell: int = AlgorithmConfig.surr_per_cell_max,
) -> list[dict]:
    """每结构组合按 |fs−格中位数| 升序取前 max_per_cell 条（v4，规格 §5.1）。

    在 clean_records 之后调用；同格记录不足上限时全保留。
    每格内保持 |fs−中位数| 升序（确定性）。
    """
    by_cell: dict[str, list[dict]] = {}
    for r in records:
        by_cell.setdefault(r["cell_key"], []).append(r)
    selected: list[dict] = []
    for key in sorted(by_cell):
        group = by_cell[key]
        fs_med = float(np.median([g["params"][0] for g in group]))
        group_sorted = sorted(group, key=lambda g: (abs(g["params"][0] - fs_med), g["device_id"]))
        selected.extend(group_sorted[:max_per_cell])
    return selected


def split_groups(
    groups: list[tuple[float, float, int, str]],
    val_n: int = AlgorithmConfig.surr_val_groups,
    test_n: int = AlgorithmConfig.surr_test_groups,
    seed: int = AlgorithmConfig.surr_split_seed,
) -> dict[tuple[float, float, int, str], str]:
    """物理器件组划分（v4，规格 §5.5）。

    组 = (eg, fl, has_pf, mark)：同 mark 的 S11/S22 两行与重复管芯绑定同侧。
    eg=0 且 fl=0 的基准组固定 train；其余随机分 val/test。
    """
    baseline = {g for g in groups if g[0] == 0.0 and g[1] == 0.0}
    others = sorted(g for g in groups if g not in baseline)
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(others))
    val_groups = {others[i] for i in perm[:val_n]}
    test_groups = {others[i] for i in perm[val_n:val_n + test_n]}
    splits: dict[tuple[float, float, int, str], str] = {}
    for g in groups:
        if g in baseline:
            splits[g] = "train"
        elif g in val_groups:
            splits[g] = "val"
        elif g in test_groups:
            splits[g] = "test"
        else:
            splits[g] = "train"
    return splits


def compute_norm_stats(records: list[dict], splits: dict) -> dict[str, list[float]]:
    """归一化统计：只用 train 组的记录（防 test 泄漏）。x 为 5 维 [eg,fl,ag,area,pf]。"""
    train = [r for r in records if splits.get(_group_tuple(r), "train") == "train"]
    x = np.array([[r["eg"], r["fl"], r["ag"], r["area_um2"], r["has_pf"]] for r in train])
    params = np.stack([r["params"] for r in train])
    z = np.concatenate([r["full_z"] for r in train])
    return {
        "x_mean": x.mean(axis=0).tolist(),
        "x_std": np.maximum(x.std(axis=0), 1e-6).tolist(),
        "params_mean": params.mean(axis=0).tolist(),
        "params_std": np.maximum(params.std(axis=0), 1e-6).tolist(),
        "z_mean": [float(z.mean())],
        "z_std": [max(float(z.std()), 1e-6)],
    }


def _group_tuple(r: dict) -> tuple[float, float, int, str]:
    return (r["eg"], r["fl"], r["has_pf"], r["mark"])
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/ml/test_surrogate_extract.py -v`
预期：9 passed

- [ ] **步骤 5：Commit**

```bash
git add backend/app/ml/surrogate/extract.py backend/tests/ml/test_surrogate_extract.py
git commit -m "feat(ml): 提取纯函数（记录/两级清洗/每格抽样/物理组划分/统计）"
```


## 任务 5：提取脚本 `extract_surrogate_dataset.py`（含 npz/meta 落地，v4）

**文件：**
- 修改：`backend/app/ml/surrogate/extract.py`（追加 `fill_sparse_base` + `assemble_npz`）
- 创建：`backend/scripts/extract_surrogate_dataset.py`
- 测试：`backend/tests/ml/test_surrogate_extract.py`（追加落地与回填测试）、`backend/tests/ml/test_extract_smoke.py`

- [ ] **步骤 1：追加失败的测试**

```python
# 追加到 backend/tests/ml/test_surrogate_extract.py
from app.ml.surrogate.baseline import BaselineTable
from app.ml.surrogate.extract import assemble_npz, fill_sparse_base


class TestFillSparseBase:
    def test_fill_writes_interpolated_baseline(self):
        recs = [
            _rec(0.0, 0.0, 700, mark="A1-1"), _rec(0.0, 0.0, 700, mark="A1-1"),
            _rec(1.0, 0.5, 700, mark="B0"),
        ]
        table = BaselineTable.build(recs)
        for r in recs:
            r["params_base"], _ = table.lookup(r["area_um2"], r["has_pf"])
        fill_sparse_base(recs, table)
        for r in recs:
            # 基准全谱恒 40 → 任意位置插值都是 40
            np.testing.assert_allclose(r["sparse_base"], 40.0)


class TestAssembleNpz:
    def test_writes_npz_and_meta(self, tmp_path):
        recs = [
            _rec(0.0, 0.0, 700, mark="A1-1"), _rec(0.0, 0.0, 5500, mark="A1-1"),
            _rec(1.0, 0.5, 700, mark="B0"), _rec(2.0, 1.0, 700, mark="C0"),
        ]
        for r in recs:
            r["params_base"] = np.zeros(7)
        groups = [(0.0, 0.0, 0, "A1-1"), (1.0, 0.5, 0, "B0"), (2.0, 1.0, 0, "C0")]
        splits = {groups[0]: "train", groups[1]: "val", groups[2]: "test"}
        stats = compute_norm_stats(recs, splits)
        assemble_npz(recs, groups, splits, stats, dropped=[], out_dir=tmp_path,
                     freq_axis=np.linspace(4.0, 7.0, 101), k=32)
        data = np.load(tmp_path / "dataset.npz")
        assert data["x_struct"].shape == (4, 5)  # [eg, fl, ag, area, pf]
        assert data["y_params"].shape == (4, 7)
        assert data["full_z"].shape == (4, 101)
        assert data["sample_pos"].shape == (4, 32)
        # 同 mark（A1-1）的两行 group_idx 相同（物理绑定）
        assert data["group_idx"][0] == data["group_idx"][1]
        import json
        meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
        assert len(meta["groups"]) == 3
        assert meta["groups"][1]["split"] == "val"
        assert meta["k"] == 32
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/ml/test_surrogate_extract.py -q`
预期：FAIL，`ImportError: cannot import name 'assemble_npz'`

- [ ] **步骤 3：实现 `fill_sparse_base` + `assemble_npz`（追加到 extract.py）**

```python
def fill_sparse_base(records: list[dict], baseline: "BaselineTable") -> None:
    """回填每条记录的 sparse_base：基准全谱在该器件采样位置上的取值（同网格直接索引）。"""
    for r in records:
        _, z_base = baseline.lookup(r["area_um2"], r["has_pf"])
        if len(z_base) == len(r["full_z"]):
            r["sparse_base"] = z_base[r["sample_pos"]].astype(np.float32)
        else:  # 网格不一致时按位置比例插值（防御分支）
            x_old = np.linspace(0, 1, len(z_base))
            x_new = r["sample_pos"] / (len(r["full_z"]) - 1)
            r["sparse_base"] = np.interp(x_new, x_old, z_base).astype(np.float32)


def assemble_npz(
    records: list[dict],
    groups: list[tuple[float, float, int, str]],
    splits: dict,
    stats: dict,
    dropped: list[dict],
    out_dir: str | Path,
    freq_axis: NDArray[np.float64],
    k: int,
) -> None:
    """写 dataset.npz + meta.json（数据契约见计划头部，v4：groups 版）。"""
    import json

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    group_list = sorted(groups)
    group_index = {g: i for i, g in enumerate(group_list)}

    np.savez_compressed(
        out / "dataset.npz",
        x_struct=np.array(
            [[r["eg"], r["fl"], r["ag"], r["area_um2"], r["has_pf"]] for r in records],
            dtype=np.float32,
        ),
        y_params=np.stack([r["params"] for r in records]).astype(np.float32),
        params_base=np.stack([r["params_base"] for r in records]).astype(np.float32),
        sample_pos=np.stack([r["sample_pos"] for r in records]).astype(np.int32),
        y_sparse=np.stack([r["sample_z"] for r in records]).astype(np.float32),
        sparse_base=np.stack([r["sparse_base"] for r in records]).astype(np.float32),
        full_z=np.stack([r["full_z"] for r in records]).astype(np.float32),
        phase=np.stack([r["phase"] for r in records]).astype(np.float32),
        group_idx=np.array([group_index[_group_tuple(r)] for r in records], dtype=np.int32),
    )
    meta = {
        "groups": [
            {"eg": g[0], "fl": g[1], "has_pf": g[2], "mark": g[3], "split": splits[g]}
            for g in group_list
        ],
        "freq_ghz": freq_axis.tolist(),
        "k": k,
        "n_freq": len(freq_axis),
        **stats,
        "dropped": [
            {"device_id": d["device_id"], "cell_key": d["cell_key"], "reason": d["reason"]}
            for d in dropped
        ],
        "config": {
            "fs_tol_mhz": AlgorithmConfig.surr_clean_fs_tol_mhz,
            "trend_sigma": AlgorithmConfig.surr_clean_trend_sigma,
            "per_cell_max": AlgorithmConfig.surr_per_cell_max,
            "default_ag": AlgorithmConfig.surr_default_ag,
        },
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
```

- [ ] **步骤 4：运行测试验证通过，然后编写提取脚本**

运行：`uv run pytest tests/ml/test_surrogate_extract.py -q`
预期：11 passed

```python
# backend/scripts/extract_surrogate_dataset.py
"""代理模型训练数据提取（规格 §5，v4）。

用法：
    uv run python scripts/extract_surrogate_dataset.py \
        --db-url sqlite:////Users/jingbozuo/.aln-data/aln-data.db \
        --data-root /Users/jingbozuo/.aln-data \
        --batch-no '#2' \
        --out ./surrogate_data
"""

from __future__ import annotations

import argparse
import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from sqlalchemy import create_engine, select

from app.config import AlgorithmConfig as AC
from app.core.sparse_sampler import read_z_mag_db
from app.ml.surrogate.baseline import BaselineTable
from app.ml.surrogate.extract import (
    _group_tuple,
    assemble_npz,
    build_device_record,
    clean_records,
    compute_norm_stats,
    fill_sparse_base,
    select_per_cell,
    split_groups,
)
from app.models import Batch, Device, MappingEntry

log = logging.getLogger("extract_surrogate")

_PARAM_COLS = list(AC.surr_param_cols)


def _fetch_rows(db_url: str, batch_no: str) -> list[dict]:
    """查询批次器件行，JOIN mapping_entries 取 has_pf/mark；剔除关联失败的行。"""
    engine = create_engine(db_url)
    cols = [
        Device.id, Device.eg, Device.fl, Device.area_um2,
        *[getattr(Device, c) for c in _PARAM_COLS],
        Device.s_param_path, Device.s_param_port, Device.mark,
        Batch.batch_no, Batch.mapping_id,
        MappingEntry.has_pf,
    ]
    stmt = (
        select(*cols)
        .select_from(Device)
        .join(Batch, Device.batch_id == Batch.id)
        .join(
            MappingEntry,
            (MappingEntry.mark == Device.mark)
            & (MappingEntry.mapping_id == Batch.mapping_id),
        )
        .where(
            Batch.batch_no == batch_no,
            Device.eg.is_not(None),
            Device.fl.is_not(None),
            Device.area_um2.is_not(None),
            Device.s_param_path.is_not(None),
        )
        .order_by(Device.id.asc())
    )
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(stmt).mappings().all()]
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="代理模型训练数据提取")
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--data-root", required=True, help="DATA_ROOT（含 files/ 子目录）")
    ap.add_argument("--batch-no", required=True)
    ap.add_argument("--out", default="./surrogate_data")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="调试：只处理前 N 条（0=全部）")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    files_dir = Path(args.data_root) / "files"

    rows = _fetch_rows(args.db_url, args.batch_no)
    if args.limit > 0:
        rows = rows[: args.limit]
    log.info("查询到 %d 条器件记录（已 ⋈ mapping_entries，关联失败已剔除）", len(rows))

    # 频率轴：从第一条可读记录确定
    freq_axis = None
    for row in rows:
        p = files_dir / str(row["batch_no"]) / str(row["s_param_path"])
        if p.exists():
            freq_axis, _ = read_z_mag_db(p, port=row.get("s_param_port") or "S11",
                                         target_n_freq=AlgorithmConfig.surr_n_freq)
            break
    if freq_axis is None:
        raise SystemExit("没有任何可读的 S 参数文件")
    log.info("频率轴: %.3f - %.3f GHz, %d 点", freq_axis[0], freq_axis[-1], len(freq_axis))

    # 多进程构建记录（build_device_record 为顶层函数，可 pickle）
    records: list[dict] = []
    with ProcessPoolExecutor(max_workers=args.workers) as exe:
        futs = [exe.submit(build_device_record, row, files_dir, freq_axis, AC.surr_k)
                for row in rows]
        for i, fut in enumerate(as_completed(futs), 1):
            rec = fut.result()
            if rec is not None:
                records.append(rec)
            if i % 2000 == 0:
                log.info("进度 %d/%d，成功 %d", i, len(rows), len(records))
    log.info("成功构建 %d 条记录", len(records))

    # 清洗（两级）
    kept, dropped = clean_records(records)
    log.info("清洗: 保留 %d，剔除 %d", len(kept), len(dropped))

    # 基准表（用全部合法基准格记录，不受抽样限制）+ 回填
    baseline = BaselineTable.build(kept)
    baseline.save(Path(args.out) / "baseline.npz")
    for r in kept:
        r["params_base"], _ = baseline.lookup(r["area_um2"], r["has_pf"])
    fill_sparse_base(kept, baseline)

    # 每格抽样（v4：≤ surr_per_cell_max 条/格）
    selected = select_per_cell(kept)
    log.info("每格抽样: %d → %d（每格 ≤ %d）", len(kept), len(selected), AlgorithmConfig.surr_per_cell_max)

    # 物理组划分 + 统计 + 落地
    groups = sorted({_group_tuple(r) for r in kept})
    splits = split_groups(groups)
    stats = compute_norm_stats(kept, splits)
    assemble_npz(selected, groups, splits, stats, dropped, args.out, freq_axis, AC.surr_k)
    n_train = sum(1 for g in groups if splits[g] == "train")
    log.info("完成: %d 物理组（train %d / val %d / test %d）→ %s",
             len(groups), n_train, AlgorithmConfig.surr_val_groups, AlgorithmConfig.surr_test_groups, args.out)


if __name__ == "__main__":
    main()
```

注意：`MappingEntry` 模型在 `app/models/` 中（表 `mapping_entries`，列 `mapping_id/mark/has_pf`）；若导入路径不同（如 `app.models.mapping`），按实际模型文件调整 import。`_fetch_rows` 用 INNER JOIN——mark 为空或关联失败的行自然被剔除（v4 规格 §5.1）。

- [ ] **步骤 5：sqlite 内存夹具冒烟测试（不依赖真实数据库）**

新建 `backend/tests/ml/test_extract_smoke.py`：

```python
"""提取链路冒烟：2 个合成 s2p + 构造行 → 清洗/抽样/基准/落地。"""

import numpy as np
import pytest

skrf = pytest.importorskip("skrf")


def _write_s2p(path, fs_ghz=4.8, fp_ghz=5.1):
    """写一个最小双端口 s2p（50Ω + fs/fp 处两个谐振扰动）。"""
    freq = np.linspace(4e9, 7e9, 301)
    s11 = np.full(len(freq), 0.05 + 0j)
    z = 50 * (1 + s11) / (1 - s11)
    z = z + 200 * np.exp(-((freq / 1e9 - fs_ghz) ** 2) / 0.002)
    z = z + 2000 * np.exp(-((freq / 1e9 - fp_ghz) ** 2) / 0.002)
    s11 = (z - 50) / (z + 50)
    lines = ["! synthetic\n# Hz S RI R 50\n"]
    for f, s in zip(freq, s11):
        lines.append(f"{f:.0f} {s.real:.6f} {s.imag:.6f} 0 0 0 0 {s.real:.6f} {s.imag:.6f}\n")
    path.write_text("".join(lines))


def _row(i, mark, pf, path_name, fs=4.8, fp=5.1):
    return {"id": i, "eg": 0.0 if pf == 0 else 1.0, "fl": 0.0, "area_um2": 700,
            "has_pf": pf, "mark": mark,
            "fs_ghz": fs, "fp_ghz": fp, "k2eff_pct": 7.0, "qs": 500.0, "qp": 400.0,
            "qs_bodeq": 450.0, "qp_bodeq": 380.0,
            "s_param_path": path_name, "s_param_port": "S11", "batch_no": "T1"}


def test_extract_end_to_end(tmp_path):
    from app.ml.surrogate.baseline import BaselineTable
    from app.ml.surrogate.extract import (
        _group_tuple, assemble_npz, build_device_record, clean_records,
        compute_norm_stats, fill_sparse_base, select_per_cell, split_groups,
    )

    files_dir = tmp_path / "files" / "T1"
    files_dir.mkdir(parents=True)
    _write_s2p(files_dir / "a.s2p")
    _write_s2p(files_dir / "b.s2p", fs_ghz=4.9, fp_ghz=5.2)

    rows = [_row(1, "A1-1", 0, "a.s2p", fs=4.8, fp=5.1),
            _row(2, "H1-1", 1, "b.s2p", fs=4.9, fp=5.2)]
    freq_axis = np.linspace(4.0, 7.0, 1001)

    recs = [build_device_record(r, tmp_path / "files", freq_axis, k=32) for r in rows]
    assert all(r is not None for r in recs)
    assert recs[0]["full_z"].shape == (1001,)
    assert recs[0]["sample_pos"].shape == (32,)
    assert recs[0]["has_pf"] == 0 and recs[1]["has_pf"] == 1
    assert recs[0]["ag"] == 1.0  # surr_default_ag 占位

    kept, dropped = clean_records(recs, fs_tol_mhz=1e9, trend_sigma=1e9)
    assert len(kept) == 2
    table = BaselineTable.build(kept)  # eg=0/fl=0 的只有 a（pf=0）
    for r in kept:
        r["params_base"], _ = table.lookup(r["area_um2"], r["has_pf"])
    fill_sparse_base(kept, table)

    selected = select_per_cell(kept, max_per_cell=5)
    assert len(selected) == 2

    groups = sorted({_group_tuple(r) for r in kept})
    splits = split_groups(groups, val_n=0, test_n=1, seed=42)
    stats = compute_norm_stats(kept, splits)
    assemble_npz(selected, groups, splits, stats, dropped, tmp_path / "out", freq_axis, 32)
    data = np.load(tmp_path / "out" / "dataset.npz")
    assert data["full_z"].shape == (2, 1001)
    assert data["x_struct"].shape == (2, 5)
    assert np.all(np.isfinite(data["full_z"]))
```

运行：`uv run pytest tests/ml/test_extract_smoke.py -v`
预期：1 passed

- [ ] **步骤 6：全量回归 + Commit**

运行：`uv run pytest tests/ml tests/core/test_deterministic_sampling.py -q`
预期：全部 passed

```bash
git add backend/app/ml/surrogate/extract.py backend/scripts/extract_surrogate_dataset.py backend/tests/ml/
git commit -m "feat(ml): 提取脚本（has_pf JOIN + 每格抽样 + 物理组落地 + 冒烟夹具）"
```


## 任务 6：torch Dataset `SurrogateDataset`

**文件：**
- 创建：`backend/app/ml/surrogate/dataset.py`
- 创建：`backend/tests/ml/conftest.py`（合成小 npz 夹具，后续任务共用）
- 测试：`backend/tests/ml/test_surrogate_dataset.py`

- [ ] **步骤 1：编写共用夹具 `conftest.py`**

```python
# backend/tests/ml/conftest.py
"""合成小 npz 夹具：6 物理组 × 4 器件，N=101，K=32（v4：5 维输入 + groups）。"""

import json

import numpy as np
import pytest


@pytest.fixture()
def tiny_npz(tmp_path):
    rng = np.random.default_rng(0)
    n_freq, k = 101, 32
    groups = [
        (0.0, 0.0, 0, "A1-1", "train"), (0.0, 0.0, 1, "G1-1", "train"),
        (1.0, 0.5, 0, "B0", "train"), (1.0, 0.5, 1, "H0", "train"),
        (2.0, 1.0, 0, "C0", "val"), (0.75, 1.25, 0, "D0", "test"),
    ]
    freq = np.linspace(4.0, 7.0, n_freq)
    recs = {"x_struct": [], "y_params": [], "params_base": [], "sample_pos": [],
            "y_sparse": [], "sparse_base": [], "full_z": [], "phase": [], "group_idx": []}
    for gi, (eg, fl, pf, _mark, _split) in enumerate(groups):
        for _ in range(4):
            area = 700 + gi * 200
            z = 40 + 5 * np.sin(freq * 2) + rng.normal(0, 0.1, n_freq) + area / 1000
            pos = np.sort(rng.choice(n_freq, k, replace=False)).astype(np.int32)
            recs["x_struct"].append([eg, fl, 1.0, area, pf])  # [eg, fl, ag, area, pf]
            recs["y_params"].append([4.8 + eg * 0.01, 5.1 + fl * 0.01, 7.0, 500, 400, 450, 380])
            recs["params_base"].append([4.8, 5.1, 7.0, 500, 400, 450, 380])
            recs["sample_pos"].append(pos)
            recs["y_sparse"].append(z[pos])
            recs["sparse_base"].append(z[pos] - 0.5)
            recs["full_z"].append(z)
            recs["phase"].append(np.zeros(n_freq))
            recs["group_idx"].append(gi)
    np.savez_compressed(
        tmp_path / "dataset.npz",
        x_struct=np.array(recs["x_struct"], dtype=np.float32),
        y_params=np.array(recs["y_params"], dtype=np.float32),
        params_base=np.array(recs["params_base"], dtype=np.float32),
        sample_pos=np.array(recs["sample_pos"]),
        y_sparse=np.array(recs["y_sparse"], dtype=np.float32),
        sparse_base=np.array(recs["sparse_base"], dtype=np.float32),
        full_z=np.array(recs["full_z"], dtype=np.float32),
        phase=np.array(recs["phase"], dtype=np.float32),
        group_idx=np.array(recs["group_idx"], dtype=np.int32),
    )
    meta = {
        "groups": [
            {"eg": g[0], "fl": g[1], "has_pf": g[2], "mark": g[3], "split": g[4]}
            for g in groups
        ],
        "freq_ghz": freq.tolist(), "k": k, "n_freq": n_freq,
        "x_mean": [0.5] * 5, "x_std": [1.0] * 5,
        "params_mean": [4.85, 5.15, 7.0, 500, 400, 450, 380],
        "params_std": [0.02, 0.02, 0.5, 50, 40, 45, 38],
        "z_mean": [42.0], "z_std": [3.0],
    }
    (tmp_path / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return tmp_path
```

- [ ] **步骤 2：编写失败的测试**

```python
# backend/tests/ml/test_surrogate_dataset.py
"""SurrogateDataset 单测。"""

import torch

from app.ml.surrogate.dataset import SurrogateDataset


class TestSurrogateDataset:
    def test_split_filtering(self, tiny_npz):
        train = SurrogateDataset(tiny_npz, split="train")
        val = SurrogateDataset(tiny_npz, split="val")
        test = SurrogateDataset(tiny_npz, split="test")
        assert len(train) == 16 and len(val) == 4 and len(test) == 4

    def test_item_shapes_and_norm(self, tiny_npz):
        ds = SurrogateDataset(tiny_npz, split="train")
        item = ds[0]
        assert item["x"].shape == (4,)
        assert item["y_params"].shape == (7,)
        assert item["y_sparse"].shape == (32,)
        assert item["params_base"].shape == (7,)
        assert item["sparse_base"].shape == (32,)
        # 归一化：y_params ≈ (原始 - mean) / std，fs 原始 4.8+eg*0.01
        assert abs(item["y_params"][0].item()) < 5.0  # 归一化后量级
        # 归一化基准同样被归一化
        expected_base0 = (4.8 - 4.85) / 0.02
        assert abs(item["params_base"][0].item() - expected_base0) < 1e-4

    def test_exposes_meta(self, tiny_npz):
        ds = SurrogateDataset(tiny_npz, split="train")
        assert ds.freq_ghz.shape == (101,)
        assert ds.k == 32
```

- [ ] **步骤 3：运行测试验证失败**

运行：`uv run pytest tests/ml/test_surrogate_dataset.py -v`
预期：FAIL，`ModuleNotFoundError: No module named 'app.ml.surrogate.dataset'`

- [ ] **步骤 4：实现 `dataset.py`**

```python
# backend/app/ml/surrogate/dataset.py
"""SurrogateDataset：加载 extract_surrogate_dataset 产出的 npz + meta.json。

归一化在 __getitem__ 内完成（统计来自 meta.json，提取时已只用 train 单元计算）。
基准（params_base / sparse_base）用同一组统计归一化，保证"基准 + 残差"在同一空间。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import Dataset


class SurrogateDataset(Dataset):
    """按网格单元 split 过滤的代理模型数据集。"""

    def __init__(self, data_dir: str | Path, split: str = "train") -> None:
        super().__init__()
        data_dir = Path(data_dir)
        meta = json.loads((data_dir / "meta.json").read_text(encoding="utf-8"))
        self.meta = meta
        self.freq_ghz = torch.tensor(meta["freq_ghz"], dtype=torch.float32)
        self.k: int = meta["k"]
        self.n_freq: int = meta["n_freq"]

        data = np.load(data_dir / "dataset.npz")
        keep_groups = {i for i, g in enumerate(meta["groups"]) if g["split"] == split}
        mask = np.array([ci in keep_groups for ci in data["group_idx"]])

        self.x = torch.from_numpy(data["x_struct"][mask]).float()
        self.y_params = torch.from_numpy(data["y_params"][mask]).float()
        self.params_base = torch.from_numpy(data["params_base"][mask]).float()
        self.y_sparse = torch.from_numpy(data["y_sparse"][mask]).float()
        self.sparse_base = torch.from_numpy(data["sparse_base"][mask]).float()
        self.full_z = torch.from_numpy(data["full_z"][mask]).float()
        self.sample_pos = torch.from_numpy(data["sample_pos"][mask]).long()
        self.group_idx = torch.from_numpy(data["group_idx"][mask]).long()

        self.x_mean = torch.tensor(meta["x_mean"], dtype=torch.float32)
        self.x_std = torch.tensor(meta["x_std"], dtype=torch.float32)
        self.p_mean = torch.tensor(meta["params_mean"], dtype=torch.float32)
        self.p_std = torch.tensor(meta["params_std"], dtype=torch.float32)
        self.z_mean = float(meta["z_mean"][0])
        self.z_std = float(meta["z_std"][0])

    def __len__(self) -> int:
        return len(self.x)

    def norm_z(self, z: torch.Tensor) -> torch.Tensor:
        return (z - self.z_mean) / self.z_std

    def denorm_z(self, z: torch.Tensor) -> torch.Tensor:
        return z * self.z_std + self.z_mean

    def denorm_params(self, p: torch.Tensor) -> torch.Tensor:
        return p * self.p_std + self.p_mean

    def __getitem__(self, idx: int) -> dict[str, torch.Tensor]:
        return {
            "x": (self.x[idx] - self.x_mean) / self.x_std,
            "y_params": (self.y_params[idx] - self.p_mean) / self.p_std,
            "params_base": (self.params_base[idx] - self.p_mean) / self.p_std,
            "y_sparse": self.norm_z(self.y_sparse[idx]),
            "sparse_base": self.norm_z(self.sparse_base[idx]),
            "group_idx": self.group_idx[idx],
        }
```

- [ ] **步骤 5：运行测试验证通过 + Commit**

运行：`uv run pytest tests/ml/test_surrogate_dataset.py -v`
预期：3 passed

```bash
git add backend/app/ml/surrogate/dataset.py backend/tests/ml/conftest.py backend/tests/ml/test_surrogate_dataset.py
git commit -m "feat(ml): SurrogateDataset（split 过滤 + 归一化 + 基准对齐）"
```

---

## 任务 7：Stage 1 模型 `BaselineResidualMLP`

**文件：**
- 创建：`backend/app/ml/surrogate/model.py`
- 测试：`backend/tests/ml/test_surrogate_model.py`

设计（规格 §6）：输入归一化 x(4) → Linear 256 → GELU → 残差块×2 → 双头（Δparams(7) / Δsparse(K)）；输出 = 基准 + 残差。**两头最后一层零初始化**，保证零残差时输出恒等于基准。

- [ ] **步骤 1：编写失败的测试**

```python
# backend/tests/ml/test_surrogate_model.py
"""Stage 1 模型单测。"""

import torch

from app.ml.surrogate.model import BaselineResidualMLP


class TestBaselineResidualMLP:
    def test_forward_shapes(self):
        model = BaselineResidualMLP(in_dim=5, hidden=64, n_res_blocks=2, n_params=7, k=32)
        out = model(torch.randn(8, 4), torch.randn(8, 7), torch.randn(8, 32))
        assert out["params"].shape == (8, 7)
        assert out["sparse"].shape == (8, 32)

    def test_zero_residual_identity(self):
        """最后一层零初始化 → 输出恒等于基准。"""
        model = BaselineResidualMLP(in_dim=5, hidden=64, n_res_blocks=2, n_params=7, k=32)
        p_base = torch.randn(4, 7)
        s_base = torch.randn(4, 32)
        out = model(torch.randn(4, 4), p_base, s_base)
        torch.testing.assert_close(out["params"], p_base)
        torch.testing.assert_close(out["sparse"], s_base)

    def test_residual_changes_output(self):
        """非零输入扰动后输出应偏离基准（证明残差通路存在）。"""
        model = BaselineResidualMLP(in_dim=5, hidden=64, n_res_blocks=2, n_params=7, k=32)
        with torch.no_grad():
            for p in model.head_params[-1].parameters():
                p.normal_(0, 0.1)
        p_base = torch.zeros(2, 7)
        s_base = torch.zeros(2, 32)
        out = model(torch.ones(2, 4), p_base, s_base)
        assert out["params"].abs().sum() > 0
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/ml/test_surrogate_model.py -v`
预期：FAIL，`ModuleNotFoundError`

- [ ] **步骤 3：实现 `model.py`**

```python
# backend/app/ml/surrogate/model.py
"""Stage 1：基准锚定残差 MLP（规格 §6）。

输入归一化结构参数，输出 = 基准 + 残差（归一化空间）。
双头最后一层零初始化 → 训练起点等价于纯基准模型（B0），
残差从零开始逐步学习，避免初期输出被随机头破坏。
"""

from __future__ import annotations

import torch
import torch.nn as nn


class _ResBlock(nn.Module):
    def __init__(self, dim: int) -> None:
        super().__init__()
        self.net = nn.Sequential(nn.Linear(dim, dim), nn.GELU(), nn.Linear(dim, dim))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x + self.net(x)


class BaselineResidualMLP(nn.Module):
    """(eg, fl, area[, ag]) → Δparams(7) + Δsparse(K)，输出基准+残差。"""

    def __init__(
        self,
        in_dim: int = 4,
        hidden: int = 256,
        n_res_blocks: int = 2,
        n_params: int = 7,
        k: int = 256,
    ) -> None:
        super().__init__()
        self.in_dim = in_dim
        self.n_params = n_params
        self.k = k

        layers: list[nn.Module] = [nn.Linear(in_dim, hidden), nn.GELU()]
        layers += [_ResBlock(hidden) for _ in range(n_res_blocks)]
        self.backbone = nn.Sequential(*layers)

        self.head_params = nn.Sequential(
            nn.Linear(hidden, hidden), nn.GELU(), nn.Linear(hidden, n_params)
        )
        self.head_sparse = nn.Sequential(
            nn.Linear(hidden, hidden), nn.GELU(), nn.Linear(hidden, k)
        )
        # 零初始化双头最后一层：零残差 → 输出恒等于基准
        for head in (self.head_params, self.head_sparse):
            nn.init.zeros_(head[-1].weight)
            nn.init.zeros_(head[-1].bias)

    def forward(
        self,
        x: torch.Tensor,  # (B, in_dim) 归一化
        params_base: torch.Tensor,  # (B, n_params) 归一化基准
        sparse_base: torch.Tensor,  # (B, k) 归一化基准
    ) -> dict[str, torch.Tensor]:
        h = self.backbone(x)
        return {
            "params": params_base + self.head_params(h),
            "sparse": sparse_base + self.head_sparse(h),
        }
```

- [ ] **步骤 4：运行测试验证通过 + Commit**

运行：`uv run pytest tests/ml/test_surrogate_model.py -v`
预期：3 passed

```bash
git add backend/app/ml/surrogate/model.py backend/tests/ml/test_surrogate_model.py
git commit -m "feat(ml): Stage 1 基准锚定残差 MLP（零初始化残差头）"
```

---

## 任务 8：Stage 1 训练脚本 `train_surrogate_stage1.py`

**文件：**
- 创建：`backend/scripts/train_surrogate_stage1.py`
- 测试：`backend/tests/ml/test_train_stage1_smoke.py`

- [ ] **步骤 1：编写脚本**

```python
# backend/scripts/train_surrogate_stage1.py
"""Stage 1 训练（规格 §6）。

用法：
    uv run python scripts/train_surrogate_stage1.py --data ./surrogate_data \
        --out ./checkpoints_surrogate --epochs 300
冒烟：
    uv run python scripts/train_surrogate_stage1.py --data ./surrogate_data \
        --out /tmp/surr_smoke --epochs 2 --smoke
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from torch.utils.data import DataLoader

from app.ml.surrogate.dataset import SurrogateDataset
from app.ml.surrogate.model import BaselineResidualMLP


def _device() -> torch.device:
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def train(data_dir: str, out_dir: str, epochs: int = 300, batch_size: int = 512,
          lr: float = 3e-4, lambda_sparse: float = 0.1, patience: int = 30,
          smoke: bool = False) -> dict:
    dev = _device()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    train_ds = SurrogateDataset(data_dir, split="train")
    val_ds = SurrogateDataset(data_dir, split="val")
    if smoke:
        epochs = min(epochs, 2)
    train_dl = DataLoader(train_ds, batch_size=batch_size, shuffle=True, drop_last=False)
    val_dl = DataLoader(val_ds, batch_size=batch_size)

    model = BaselineResidualMLP(in_dim=5, hidden=256, n_res_blocks=2,
                                n_params=7, k=train_ds.k).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)

    def run_epoch(dl, train_mode: bool) -> float:
        model.train(train_mode)
        total, n = 0.0, 0
        with torch.set_grad_enabled(train_mode):
            for batch in dl:
                x = batch["x"].to(dev)
                yp = batch["y_params"].to(dev)
                ys = batch["y_sparse"].to(dev)
                pb = batch["params_base"].to(dev)
                sb = batch["sparse_base"].to(dev)
                out_pred = model(x, pb, sb)
                loss = torch.nn.functional.mse_loss(out_pred["params"], yp) \
                    + lambda_sparse * torch.nn.functional.mse_loss(out_pred["sparse"], ys)
                if train_mode:
                    opt.zero_grad()
                    loss.backward()
                    opt.step()
                total += loss.item() * len(x)
                n += len(x)
        return total / max(n, 1)

    best_val, best_state, bad = float("inf"), None, 0
    history = []
    for epoch in range(1, epochs + 1):
        tr = run_epoch(train_dl, True)
        va = run_epoch(val_dl, False)
        sched.step()
        history.append({"epoch": epoch, "train": tr, "val": va})
        if va < best_val - 1e-6:
            best_val, bad = va, 0
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
        if epoch % 10 == 0 or epoch == 1:
            print(f"epoch {epoch}: train={tr:.5f} val={va:.5f} best={best_val:.5f}")
        if bad >= patience:
            print(f"early stop at epoch {epoch}")
            break

    if best_state is not None:
        model.load_state_dict(best_state)
    torch.save(
        {"model_state": model.state_dict(), "in_dim": 5, "hidden": 256,
         "n_res_blocks": 2, "n_params": 7, "k": train_ds.k, "lambda_sparse": lambda_sparse},
        out / "stage1.pt",
    )
    (out / "stage1_history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")
    print(f"saved: {out / 'stage1.pt'} (best val={best_val:.5f})")
    return {"best_val": best_val, "epochs_run": len(history)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default="./checkpoints_surrogate")
    ap.add_argument("--epochs", type=int, default=300)
    ap.add_argument("--batch-size", type=int, default=512)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--lambda-sparse", type=float, default=0.1)
    ap.add_argument("--patience", type=int, default=30)
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args()
    train(args.data, args.out, args.epochs, args.batch_size, args.lr,
          args.lambda_sparse, args.patience, args.smoke)


if __name__ == "__main__":
    main()
```

- [ ] **步骤 2：编写冒烟测试**

```python
# backend/tests/ml/test_train_stage1_smoke.py
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
```

- [ ] **步骤 3：运行冒烟测试验证通过**

运行：`uv run pytest tests/ml/test_train_stage1_smoke.py -v`
预期：1 passed（torch 前向/反向在 CPU/MPS 上均可运行）

- [ ] **步骤 4：Commit**

```bash
git add backend/scripts/train_surrogate_stage1.py backend/tests/ml/test_train_stage1_smoke.py
git commit -m "feat(ml): Stage 1 训练脚本（早停 + cosine + 冒烟测试）"
```

---

## 任务 9：B3 轻量基线 `train_baseline_b3.py`

**文件：**
- 创建：`backend/scripts/train_baseline_b3.py`
- 测试：`backend/tests/ml/test_baseline_b3.py`

规格 §8：B3 = 结构参数 → 7 性能参数的 ExtraTrees 回归，只在 train 单元训练，报告 test 单元 MAE；Stage 1 的 params 头必须显著优于它。

- [ ] **步骤 1：编写脚本**

```python
# backend/scripts/train_baseline_b3.py
"""B3 轻量基线：ExtraTrees 结构参数 → 性能参数（规格 §8 对照基线）。

用法：
    uv run python scripts/train_baseline_b3.py --data ./surrogate_data \
        --out ./checkpoints_surrogate
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import ExtraTreesRegressor

from app.config import AlgorithmConfig as AC

_PARAM_COLS = list(AlgorithmConfig.surr_param_cols)


def _load_split(data_dir: str, split: str) -> tuple[np.ndarray, np.ndarray]:
    import json as _json

    data = np.load(Path(data_dir) / "dataset.npz")
    meta = _json.loads((Path(data_dir) / "meta.json").read_text(encoding="utf-8"))
    keep = {i for i, g in enumerate(meta["groups"]) if g["split"] == split}
    mask = np.array([ci in keep for ci in data["group_idx"]])
    return data["x_struct"][mask][:, [0, 1, 3, 4]], data["y_params"][mask]  # 输入 eg/fl/area/pf（ag 恒值不用）


def run(data_dir: str, out_dir: str, n_estimators: int = 300) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    x_tr, y_tr = _load_split(data_dir, "train")
    x_te, y_te = _load_split(data_dir, "test")

    model = ExtraTreesRegressor(n_estimators=n_estimators, n_jobs=-1, random_state=42)
    model.fit(x_tr, y_tr)
    pred = model.predict(x_te)

    mae = np.abs(pred - y_te).mean(axis=0)
    report = {
        "model": "ExtraTreesRegressor",
        "n_train": int(len(x_tr)), "n_test": int(len(x_te)),
        "mae": {col: float(m) for col, m in zip(_PARAM_COLS, mae)},
        "fs_mae_mhz": float(mae[0] * 1000),
        "fp_mae_mhz": float(mae[1] * 1000),
    }
    joblib.dump(model, out / "b3_extratrees.joblib")
    (out / "b3_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default="./checkpoints_surrogate")
    ap.add_argument("--n-estimators", type=int, default=300)
    args = ap.parse_args()
    run(args.data, args.out, args.n_estimators)


if __name__ == "__main__":
    main()
```

- [ ] **步骤 2：编写测试**

```python
# backend/tests/ml/test_baseline_b3.py
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
```

- [ ] **步骤 3：运行测试验证通过 + Commit**

运行：`uv run pytest tests/ml/test_baseline_b3.py -v`
预期：1 passed

```bash
git add backend/scripts/train_baseline_b3.py backend/tests/ml/test_baseline_b3.py
git commit -m "feat(ml): B3 ExtraTrees 轻量基线（NN 必要性检验对照）"
```

---

## 任务 10：Stage 2 训练脚本 `train_stage2_surrogate.py`

**文件：**
- 创建：`backend/scripts/train_stage2_surrogate.py`
- 测试：`backend/tests/ml/test_train_stage2_smoke.py`

复用现有 `app.ml.sparse.reconstructor.SparseReconstructor`（接口已核实：`cond(B,5) + samples(B,K,2) + freq_target(B,N) + z_baseline(B,N) → z_pred(B,N)`，K 变长无关）。训练对直接来自 npz：`sample_pos` + `y_sparse` 给稀疏点，`full_z` 给目标，cond 取真值 `[fs, fp, Qs, Qp, kt2]`（y_params 列序 fs,fp,kt2,qs,qp,... → 索引 `[0,1,3,4,2]`）。**从头训练**（308nm checkpoint 已删除）。Step B（用 Stage 1 预测点微调）在评估显示需要时再做，见任务 13。

- [ ] **步骤 1：编写脚本**

```python
# backend/scripts/train_stage2_surrogate.py
"""Stage 2 训练：稀疏点 + cond → 全谱（规格 §7，主路径=从头训练）。

用法：
    uv run python scripts/train_stage2_surrogate.py --data ./surrogate_data \
        --out ./checkpoints_surrogate --epochs 200
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset

from app.core.sparse_sampler import baseline_interpolate
from app.ml.sparse.reconstructor import SparseReconstructor

_COND_IDX = [0, 1, 3, 4, 2]  # y_params 中 [fs, fp, Qs, Qp, kt2] 的列索引


class Stage2Dataset(Dataset):
    """npz → reconstructor 训练对（z 用原始 dB，与 reconstructor 既有设计一致）。"""

    def __init__(self, data_dir: str | Path, split: str = "train") -> None:
        data_dir = Path(data_dir)
        meta = json.loads((data_dir / "meta.json").read_text(encoding="utf-8"))
        data = np.load(data_dir / "dataset.npz")
        keep = {i for i, g in enumerate(meta["groups"]) if g["split"] == split}
        mask = np.array([ci in keep for ci in data["group_idx"]])
        self.freq = np.asarray(meta["freq_ghz"], dtype=np.float32)
        self.cond = torch.from_numpy(data["y_params"][mask][:, _COND_IDX]).float()
        self.samples = np.stack(
            [self.freq[data["sample_pos"][mask]], data["y_sparse"][mask]], axis=-1
        ).astype(np.float32)  # (M, K, 2)
        self.target = torch.from_numpy(data["full_z"][mask]).float()
        self.z_baseline = torch.from_numpy(
            np.stack([
                baseline_interpolate(s[:, 0], s[:, 1], self.freq) for s in self.samples
            ])
        ).float()

    def __len__(self) -> int:
        return len(self.cond)

    def __getitem__(self, idx: int) -> dict[str, torch.Tensor]:
        return {
            "cond": self.cond[idx],
            "samples": torch.from_numpy(self.samples[idx]),
            "freq_target": torch.from_numpy(self.freq),
            "z_baseline": self.z_baseline[idx],
            "target": self.target[idx],
        }


def train(data_dir: str, out_dir: str, epochs: int = 200, batch_size: int = 256,
          lr: float = 1e-3, patience: int = 30, smoke: bool = False) -> dict:
    dev = (
        torch.device("mps") if torch.backends.mps.is_available()
        else torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")
    )
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    train_dl = DataLoader(Stage2Dataset(data_dir, "train"), batch_size=batch_size, shuffle=True)
    val_dl = DataLoader(Stage2Dataset(data_dir, "val"), batch_size=batch_size)
    if smoke:
        epochs = min(epochs, 2)

    model = SparseReconstructor(d_model=64, n_encoder_layers=4, n_heads=4, dropout=0.1).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)

    def run_epoch(dl, train_mode: bool) -> float:
        model.train(train_mode)
        total, n = 0.0, 0
        with torch.set_grad_enabled(train_mode):
            for b in dl:
                cond = b["cond"].to(dev)
                samples = b["samples"].to(dev)
                freq_t = b["freq_target"].to(dev)
                zb = b["z_baseline"].to(dev)
                target = b["target"].to(dev)
                pred = model(cond, samples, freq_t, z_baseline=zb)
                loss = torch.nn.functional.mse_loss(pred, target)
                if train_mode:
                    opt.zero_grad()
                    loss.backward()
                    opt.step()
                total += loss.item() * len(cond)
                n += len(cond)
        return total / max(n, 1)

    best_val, best_state, bad = float("inf"), None, 0
    history = []
    for epoch in range(1, epochs + 1):
        tr = run_epoch(train_dl, True)
        va = run_epoch(val_dl, False)
        sched.step()
        history.append({"epoch": epoch, "train": tr, "val": va})
        if va < best_val - 1e-6:
            best_val, bad = va, 0
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
        if epoch % 10 == 0 or epoch == 1:
            print(f"epoch {epoch}: train={tr:.4f} val={va:.4f} best={best_val:.4f}")
        if bad >= patience:
            print(f"early stop at epoch {epoch}")
            break

    if best_state is not None:
        model.load_state_dict(best_state)
    torch.save(model.state_dict(), out / "stage2.pt")
    (out / "stage2_config.json").write_text(
        json.dumps({"d_model": 64, "n_encoder_layers": 4, "n_heads": 4, "dropout": 0.1,
                    "max_freq": 10.0}, indent=2),
        encoding="utf-8",
    )
    (out / "stage2_history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")
    print(f"saved: {out / 'stage2.pt'} (best val={best_val:.4f})")
    return {"best_val": best_val, "epochs_run": len(history)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default="./checkpoints_surrogate")
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--batch-size", type=int, default=256)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--patience", type=int, default=30)
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args()
    train(args.data, args.out, args.epochs, args.batch_size, args.lr,
          args.patience, args.smoke)


if __name__ == "__main__":
    main()
```

- [ ] **步骤 2：编写冒烟测试**

```python
# backend/tests/ml/test_train_stage2_smoke.py
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
```

- [ ] **步骤 3：运行测试验证通过 + Commit**

运行：`uv run pytest tests/ml/test_train_stage2_smoke.py -v`
预期：2 passed

```bash
git add backend/scripts/train_stage2_surrogate.py backend/tests/ml/test_train_stage2_smoke.py
git commit -m "feat(ml): Stage 2 从头训练脚本（确定性稀疏点 + cond 条件）"
```

---

## 任务 11：端到端推理 `SurrogatePredictor`

**文件：**
- 创建：`backend/app/ml/surrogate/infer.py`
- 测试：`backend/tests/ml/test_surrogate_infer.py`

推理链路（规格 §4/§6/§7）：输入 (eg, fl, area[, ag]) → 查基准 → Stage 1 **两遍前向**（第一遍出 params，用预测的 fs/fp 算采样位置并查 sparse_base，第二遍出 sparse_z）→ spline 基线 → Stage 2 → 全谱。

- [ ] **步骤 1：编写失败的测试**

```python
# backend/tests/ml/test_surrogate_infer.py
"""端到端推理契约测试：随机初始化小模型 → 输出形状/有限性/参数合理性。"""

import numpy as np
import torch

from app.ml.surrogate.infer import SurrogatePredictor
from app.ml.surrogate.model import BaselineResidualMLP
from app.ml.sparse.reconstructor import SparseReconstructor


def _make_ckpts(tiny_npz, tmp_path):
    s1 = BaselineResidualMLP(in_dim=5, hidden=32, n_res_blocks=1, n_params=7, k=32)
    torch.save({"model_state": s1.state_dict(), "in_dim": 5, "hidden": 32,
                "n_res_blocks": 1, "n_params": 7, "k": 32}, tmp_path / "stage1.pt")
    s2 = SparseReconstructor(d_model=32, n_encoder_layers=1, n_heads=2, dropout=0.0)
    torch.save(s2.state_dict(), tmp_path / "stage2.pt")
    import json
    (tmp_path / "stage2_config.json").write_text(
        json.dumps({"d_model": 32, "n_encoder_layers": 1, "n_heads": 2, "dropout": 0.0}),
        encoding="utf-8")
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
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/ml/test_surrogate_infer.py -v`
预期：FAIL，`ModuleNotFoundError: No module named 'app.ml.surrogate.infer'`

- [ ] **步骤 3：实现 `infer.py`**

```python
# backend/app/ml/surrogate/infer.py
"""端到端推理：结构参数 → Stage 1（基准+残差）→ 稀疏点 → Stage 2 → 全谱。

Stage 1 需要两遍前向：第一遍出性能参数（含 fs/fp），
用预测的 fs/fp 按确定性规则生成采样位置并查 sparse_base，第二遍出稀疏点。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import torch

from app.config import AlgorithmConfig as AC
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
            in_dim=ckpt["in_dim"], hidden=ckpt["hidden"],
            n_res_blocks=ckpt["n_res_blocks"], n_params=ckpt["n_params"], k=ckpt["k"],
        ).to(self.dev)
        self.stage1.load_state_dict(ckpt["model_state"])
        self.stage1.eval()

        cfg = json.loads(Path(stage2_config).read_text(encoding="utf-8"))
        self.stage2 = SparseReconstructor(**cfg).to(self.dev)
        self.stage2.load_state_dict(torch.load(stage2_path, map_location=self.dev,
                                               weights_only=True))
        self.stage2.eval()

    @torch.no_grad()
    def predict(self, eg: float, fl: float, area_um2: float, pf: float = 0.0,
                ag: float = AlgorithmConfig.surr_default_ag) -> dict:
        x = (np.array([eg, fl, ag, area_um2, pf], dtype=np.float32) - self.x_mean) / self.x_std
        x_t = torch.from_numpy(x).float().unsqueeze(0).to(self.dev)

        params_base, z_base_full = self.baseline.lookup(area_um2, pf)
        pb = (torch.tensor(params_base, dtype=torch.float32, device=self.dev) - self.p_mean) \
            / self.p_std
        pb = pb.unsqueeze(0)

        # 第一遍：性能参数（sparse_base 用零，params 头不依赖它）
        out1 = self.stage1(x_t, pb, torch.zeros(1, self.k, device=self.dev))
        params = (out1["params"][0] * self.p_std + self.p_mean).cpu().numpy()
        fs, fp = float(params[0]), float(params[1])

        # 预测 fs/fp → 采样位置 → sparse_base（dB → 归一化）
        pos = deterministic_sample_positions(
            self.freq, fs, fp, k=self.k,
            dense_ratio=AlgorithmConfig.surr_dense_ratio, transition_ratio=AlgorithmConfig.surr_transition_ratio,
            dense_half_bw=AlgorithmConfig.surr_dense_half_bw, transition_half_bw=AlgorithmConfig.surr_transition_half_bw,
        )
        sparse_base_db = z_base_full[pos] if len(z_base_full) == len(self.freq) else np.interp(
            pos / (len(self.freq) - 1), np.linspace(0, 1, len(z_base_full)), z_base_full
        )
        sb = torch.from_numpy((sparse_base_db - self.z_mean) / self.z_std).float() \
            .unsqueeze(0).to(self.dev)

        # 第二遍：稀疏点
        out2 = self.stage1(x_t, pb, sb)
        sparse_z = (out2["sparse"][0].cpu().numpy() * self.z_std + self.z_mean).astype(np.float64)

        # Stage 2：稀疏点 + cond → 全谱
        cond = torch.tensor(params[_COND_IDX], dtype=torch.float32, device=self.dev).unsqueeze(0)
        sample_freq = self.freq[pos]
        samples = torch.from_numpy(
            np.stack([sample_freq, sparse_z], axis=-1)
        ).float().unsqueeze(0).to(self.dev)
        zb = torch.from_numpy(
            baseline_interpolate(sample_freq, sparse_z, self.freq)
        ).float().unsqueeze(0).to(self.dev)
        freq_t = torch.from_numpy(self.freq).float().to(self.dev)
        z_pred = self.stage2(cond, samples, freq_t, z_baseline=zb)[0].cpu().numpy()

        return {
            "params": {c: float(params[i]) for i, c in enumerate(_PARAM_COLS)},
            "freq_ghz": self.freq,
            "z_db": z_pred,
            "sample_freq": sample_freq,
            "sample_z": sparse_z,
        }
```

- [ ] **步骤 4：运行测试验证通过 + Commit**

运行：`uv run pytest tests/ml/test_surrogate_infer.py -v`
预期：1 passed

```bash
git add backend/app/ml/surrogate/infer.py backend/tests/ml/test_surrogate_infer.py
git commit -m "feat(ml): 端到端推理器（两遍前向 + Stage 2 级联）"
```

---

## 任务 12：推理 CLI `predict_spectrum.py`

**文件：**
- 创建：`backend/scripts/predict_spectrum.py`

- [ ] **步骤 1：编写脚本**

```python
# backend/scripts/predict_spectrum.py
"""代理模型推理 CLI：输入结构参数，输出性能参数 + Z11(dB) 全谱图/CSV。

用法：
    uv run python scripts/predict_spectrum.py --eg 1.0 --fl 0.5 --area 1500 \
        --data ./surrogate_data --ckpt ./checkpoints_surrogate --out ./pred_demo
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from app.ml.surrogate.infer import SurrogatePredictor


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--eg", type=float, required=True)
    ap.add_argument("--fl", type=float, required=True)
    ap.add_argument("--area", type=float, required=True)
    ap.add_argument("--pf", type=int, default=0, choices=[0, 1], help="massloading 有无（0/1）")
    ap.add_argument("--ag", type=float, default=1.0)
    ap.add_argument("--data", default="./surrogate_data")
    ap.add_argument("--ckpt", default="./checkpoints_surrogate")
    ap.add_argument("--out", default="./pred_out")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    data, ckpt = Path(args.data), Path(args.ckpt)

    pred = SurrogatePredictor(
        stage1_path=ckpt / "stage1.pt",
        stage2_path=ckpt / "stage2.pt",
        stage2_config=ckpt / "stage2_config.json",
        baseline_path=data / "baseline.npz",
        meta_path=data / "meta.json",
    )
    result = pred.predict(args.eg, args.fl, args.area, args.pf, args.ag)

    print(json.dumps(result["params"], ensure_ascii=False, indent=2))

    with open(out_dir / "spectrum.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["freq_ghz", "z_db"])
        w.writerows(zip(result["freq_ghz"], result["z_db"]))

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(result["freq_ghz"], result["z_db"], lw=1.2, label="surrogate")
    ax.plot(result["sample_freq"], result["sample_z"], ".", ms=3, label="sparse pts")
    ax.set_xlabel("Frequency (GHz)")
    ax.set_ylabel("|Z11| (dB)")
    ax.set_title(f"EG={args.eg} FL={args.fl} area={args.area} µm²")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_dir / "spectrum.png", dpi=150)
    print(f"saved: {out_dir / 'spectrum.png'} , {out_dir / 'spectrum.csv'}")


if __name__ == "__main__":
    main()
```

- [ ] **步骤 2：手动冒烟（真实 checkpoint 出来前先跳过 CLI 测试，由任务 11 的契约测试覆盖）**

运行：`uv run ruff check backend/scripts/predict_spectrum.py backend/app/ml/surrogate/`
预期：无错误

- [ ] **步骤 3：Commit**

```bash
git add backend/scripts/predict_spectrum.py
git commit -m "feat(ml): 推理 CLI（结构参数 → 性能参数 + 全谱图/CSV）"
```

---

## 任务 13：评估脚本 `evaluate_surrogate.py`

**文件：**
- 创建：`backend/scripts/evaluate_surrogate.py`

三层指标 + B0/B2/B3 对照（规格 §8），全部在 test 网格单元上：

- [ ] **步骤 1：编写脚本**

```python
# backend/scripts/evaluate_surrogate.py
"""代理模型评估（规格 §8）：三层指标 + B0/B2/B3 对照 + 报告图。

用法：
    uv run python scripts/evaluate_surrogate.py --data ./surrogate_data \
        --ckpt ./checkpoints_surrogate --out ./eval_out
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from app.config import AlgorithmConfig as AC
from app.core.sparse_sampler import deterministic_sample_positions
from app.ml.surrogate.baseline import BaselineTable
from app.ml.surrogate.infer import SurrogatePredictor

_PARAM_COLS = list(AlgorithmConfig.surr_param_cols)


def _region_masks(freq: np.ndarray, fs: float, fp: float) -> dict[str, np.ndarray]:
    """按真值 fs/fp 划评估区域（与采样规则同边界）。"""
    d = fp - fs
    dense = (freq >= fs - 0.5 * d) & (freq <= fp + 0.5 * d)
    trans = ((freq >= fs - 1.5 * d) & (freq < fs - 0.5 * d)) | (
        (freq > fp + 0.5 * d) & (freq <= fp + 1.5 * d))
    far = ~(dense | trans)
    return {"dense": dense, "transition": trans, "far": far}


def _rmse(a: np.ndarray, b: np.ndarray, mask: np.ndarray | None = None) -> float:
    if mask is not None:
        if mask.sum() == 0:
            return float("nan")
        a, b = a[mask], b[mask]
    return float(np.sqrt(np.mean((a - b) ** 2)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="./surrogate_data")
    ap.add_argument("--ckpt", default="./checkpoints_surrogate")
    ap.add_argument("--out", default="./eval_out")
    ap.add_argument("--max-devices", type=int, default=0, help="调试：最多评估 N 个器件")
    args = ap.parse_args()

    data_dir, ckpt_dir = Path(args.data), Path(args.ckpt)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    meta = json.loads((data_dir / "meta.json").read_text(encoding="utf-8"))
    freq = np.asarray(meta["freq_ghz"])
    data = np.load(data_dir / "dataset.npz")
    test_groups = {i for i, g in enumerate(meta["groups"]) if g["split"] == "test"}
    mask = np.array([ci in test_groups for ci in data["group_idx"]])
    idx_all = np.where(mask)[0]
    if args.max_devices > 0:
        idx_all = idx_all[: args.max_devices]

    pred = SurrogatePredictor(
        ckpt_dir / "stage1.pt", ckpt_dir / "stage2.pt", ckpt_dir / "stage2_config.json",
        data_dir / "baseline.npz", data_dir / "meta.json",
    )
    baseline = BaselineTable.load(data_dir / "baseline.npz")
    b3 = joblib.load(ckpt_dir / "b3_extratrees.joblib") if (ckpt_dir / "b3_extratrees.joblib").exists() else None

    # B2 准备：train 行按 (eg, fl, pf, area) 格求均值谱（v4：格键取自 x_struct）
    train_groups = {i for i, g in enumerate(meta["groups"]) if g["split"] == "train"}
    tr_mask = np.array([ci in train_groups for ci in data["group_idx"]])
    cell_mean_z: dict[tuple, np.ndarray] = {}
    for i in np.where(tr_mask)[0]:
        eg_i, fl_i, _ag, area_i, pf_i = data["x_struct"][i]
        key = (float(eg_i), float(fl_i), float(pf_i), float(area_i))
        cell_mean_z.setdefault(key, []).append(data["full_z"][i])
    cell_mean_z = {key: np.mean(v, axis=0) for key, v in cell_mean_z.items()}

    rows = []
    for i in idx_all:
        true_z = data["full_z"][i]
        true_params = data["y_params"][i]
        eg, fl, _ag, area, pf = data["x_struct"][i]
        r = pred.predict(float(eg), float(fl), float(area), float(pf))
        pred_z = r["z_db"]
        pred_params = np.array([r["params"][c] for c in _PARAM_COLS])
        fs_t, fp_t = float(true_params[0]), float(true_params[1])
        regions = _region_masks(freq, fs_t, fp_t)

        # B0：纯基准
        _, z_b0 = baseline.lookup(float(area), float(pf))
        # B2：同 (area, pf) train 格 IDW 插值（排除本格）
        neighbors = [
            (key, mz) for key, mz in cell_mean_z.items()
            if key[3] == float(area) and key[2] == float(pf)
            and not (key[0] == float(eg) and key[1] == float(fl))
        ]
        if neighbors:
            w, z_acc = 0.0, np.zeros_like(true_z)
            for key, mz in neighbors:
                d2 = (key[0] - float(eg)) ** 2 + (key[1] - float(fl)) ** 2
                wi = 1.0 / max(d2, 1e-6)
                z_acc += wi * mz
                w += wi
            z_b2 = z_acc / w
        else:
            z_b2 = z_b0

        # 预测谱重提取 fs/fp（argmin/argmax）
        fs_re = float(freq[int(np.argmin(pred_z))])
        fp_re = float(freq[int(np.argmax(pred_z))])

        row = {
            "device_idx": int(i),
            "cell": {"eg": float(eg), "fl": float(fl), "area": float(area), "pf": float(pf)},
            "fs_err_mhz": abs(float(pred_params[0]) - fs_t) * 1000,
            "fp_err_mhz": abs(float(pred_params[1]) - fp_t) * 1000,
            "fs_reextract_err_mhz": abs(fs_re - fs_t) * 1000,
            "rmse_full": _rmse(pred_z, true_z),
            **{f"rmse_{k}": _rmse(pred_z, true_z, m) for k, m in regions.items()},
            "rmse_b0": _rmse(z_b0, true_z),
            "rmse_b2": _rmse(z_b2, true_z),
        }
        if b3 is not None:
            p3 = b3.predict(np.array([[eg, fl, area, pf]]))[0]
            row["b3_fs_err_mhz"] = abs(float(p3[0]) - fs_t) * 1000
            row["b3_fp_err_mhz"] = abs(float(p3[1]) - fp_t) * 1000
        rows.append(row)

    def _mean(key: str) -> float:
        vals = [r[key] for r in rows if r[key] == r[key]]  # 去 NaN
        return float(np.mean(vals)) if vals else float("nan")

    def _mean_pf(key: str, pf_val: float) -> float:
        vals = [r[key] for r in rows if r["cell"]["pf"] == pf_val and r[key] == r[key]]
        return float(np.mean(vals)) if vals else float("nan")

    report = {
        "n_test_devices": len(rows),
        "n_test_groups": len(test_groups),
        "rmse_full_db_pf0": _mean_pf("rmse_full", 0.0),
        "rmse_full_db_pf1": _mean_pf("rmse_full", 1.0),
        "fs_mae_mhz_pf0": _mean_pf("fs_err_mhz", 0.0),
        "fs_mae_mhz_pf1": _mean_pf("fs_err_mhz", 1.0),
        "fs_mae_mhz": _mean("fs_err_mhz"),
        "fp_mae_mhz": _mean("fp_err_mhz"),
        "fs_reextract_mae_mhz": _mean("fs_reextract_err_mhz"),
        "rmse_full_db": _mean("rmse_full"),
        "rmse_dense_db": _mean("rmse_dense"),
        "rmse_transition_db": _mean("rmse_transition"),
        "rmse_far_db": _mean("rmse_far"),
        "rmse_b0_db": _mean("rmse_b0"),
        "rmse_b2_db": _mean("rmse_b2"),
        "improvement_vs_b0_pct": (1 - _mean("rmse_full") / _mean("rmse_b0")) * 100
        if _mean("rmse_b0") > 0 else float("nan"),
        "b3_fs_mae_mhz": _mean("b3_fs_err_mhz") if b3 else None,
        "targets": {"fs_mae_mhz": 10, "fp_mae_mhz": 15, "rmse_full_db": 0.5,
                    "improvement_vs_b0_pct": 50},
    }
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    # 图 1：随机 6 个 test 器件 预测 vs 真值
    rng = np.random.default_rng(0)
    pick = rng.choice(len(rows), size=min(6, len(rows)), replace=False)
    fig, axes = plt.subplots(2, 3, figsize=(15, 7))
    for ax, ri in zip(axes.flat, pick):
        i = rows[ri]["device_idx"]
        eg, fl, _ag, area, pf = data["x_struct"][i]
        r = pred.predict(float(eg), float(fl), float(area), float(pf))
        ax.plot(freq, data["full_z"][i], lw=1, label="true")
        ax.plot(freq, r["z_db"], lw=1, label="pred")
        ax.set_title(f"eg={eg} fl={fl} a={int(area)}", fontsize=9)
        ax.legend(fontsize=7)
    fig.suptitle("Surrogate vs True (test cells)")
    fig.tight_layout()
    fig.savefig(out_dir / "pred_vs_true.png", dpi=140)

    # 图 2：RMSE 随 (eg, fl) 热图（每面积档一张，取档数 ≤4）
    areas = sorted({r["cell"]["area"] for r in rows})[:4]
    fig, axes = plt.subplots(1, len(areas), figsize=(4 * len(areas), 4), squeeze=False)
    for ax, area in zip(axes.flat, areas):
        grid = np.full((20, 20), np.nan)
        egs = sorted({r["cell"]["eg"] for r in rows})
        fls = sorted({r["cell"]["fl"] for r in rows})
        for r in rows:
            if r["cell"]["area"] != area:
                continue
            grid[fls.index(r["cell"]["fl"]), egs.index(r["cell"]["eg"])] = r["rmse_full"]
        im = ax.imshow(grid, origin="lower", aspect="auto",
                       extent=[min(egs), max(egs), min(fls), max(fls)])
        ax.set_title(f"area={int(area)}")
        ax.set_xlabel("eg")
        ax.set_ylabel("fl")
        fig.colorbar(im, ax=ax, label="RMSE dB")
    fig.suptitle("Full-spectrum RMSE over design grid (test)")
    fig.tight_layout()
    fig.savefig(out_dir / "rmse_heatmap.png", dpi=140)
    print(f"saved: {out_dir}")


if __name__ == "__main__":
    main()
```

- [ ] **步骤 2：ruff 检查 + Commit**

运行：`uv run ruff check backend/scripts/evaluate_surrogate.py`
预期：无错误

```bash
git add backend/scripts/evaluate_surrogate.py
git commit -m "feat(ml): 评估脚本（三层指标 + B0/B2/B3 对照 + 热图）"
```

---

## 任务 14：真实数据运行手册（手动验证，非自动化测试）

在 worktree `backend/` 下依次执行；每步核对预期输出。**这是验收关卡**（规格 §8 目标）。

- [ ] **步骤 1：真实数据提取（约 10–30 分钟，38,400 条 × skrf 读取 / 8 进程）**

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-ml/backend
uv run python scripts/extract_surrogate_dataset.py \
    --db-url sqlite:////Users/jingbozuo/.aln-data/aln-data.db \
    --data-root /Users/jingbozuo/.aln-data \
    --batch-no '#2' \
    --out ./surrogate_data \
    --workers 8
```

预期输出日志：
- `查询到 ≈37200 条器件记录`（38,400 − 约 1,200 空 mark 关联失败）
- `频率轴: 4.000 - 7.000 GHz, 1001 点`
- `成功构建 ≈37000+ 条记录`
- `清洗: 保留 ≈31000–35000，剔除 ≈5–15%`
- `每格抽样: ≈33000 → ≈2500–2900（每格 ≤ 5）`
- `完成: ≈288 物理组（train ≈230 / val 29 / test 29）`

产出检查：`surrogate_data/dataset.npz`（抽样后约 15–20MB）、`meta.json`（≈288 个 groups + dropped 清单）、`baseline.npz`（2 pf × 12 面积档 = 24 档）。

调试建议：先加 `--limit 200` 跑通全流程再全量。

- [ ] **步骤 2：B3 轻量基线（分钟级）**

```bash
uv run python scripts/train_baseline_b3.py --data ./surrogate_data --out ./checkpoints_surrogate
```

预期：`b3_report.json` 给出 test 格 fs/fp MAE（MHz）。**记录该数值——Stage 1 必须显著优于它。**

- [ ] **步骤 3：Stage 1 训练（MPS，约 10–30 分钟）**

```bash
uv run python scripts/train_surrogate_stage1.py --data ./surrogate_data \
    --out ./checkpoints_surrogate --epochs 300
```

预期：val loss 持续下降并早停；`stage1.pt` + `stage1_history.json` 产出。

- [ ] **步骤 4：Stage 2 训练（MPS，约 1–3 小时）**

```bash
uv run python scripts/train_stage2_surrogate.py --data ./surrogate_data \
    --out ./checkpoints_surrogate --epochs 200
```

预期：`stage2.pt` + `stage2_config.json` + `stage2_history.json` 产出；val MSE（dB²）降至个位数以下。

- [ ] **步骤 5：评估**

```bash
uv run python scripts/evaluate_surrogate.py --data ./surrogate_data \
    --ckpt ./checkpoints_surrogate --out ./eval_out
```

核对规格 §8 目标：

| 指标 | 目标 | 记录实际值 |
|---|---|---|
| fs MAE | ≤ 10 MHz | |
| fp MAE | ≤ 15 MHz | |
| 全谱 RMSE | ≤ 0.5 dB（分区报告 dense/transition/far） | |
| 重提取 fs MAE | ≤ 15 MHz | |
| 相比 B0 提升 | ≥ 50% | |
| Stage 1 vs B3 | 显著优于 | |

未达标时的迭代顺序：① 若 Stage 1 不优于 B3 → 增大模型/调 λ/查清洗；② 若稀疏点 RMSE 高 → 查基准表与位置规则；③ 若全谱误差集中在 dense 区 → 启动规格 §7 Step B（用 Stage 1 预测点微调 Stage 2）。

- [ ] **步骤 6：推理演示**

```bash
uv run python scripts/predict_spectrum.py --eg 1.0 --fl 0.5 --area 1500 \
    --data ./surrogate_data --ckpt ./checkpoints_surrogate --out ./pred_demo
```

预期：打印 7 个性能参数；`pred_demo/spectrum.png` 显示连续阻抗谱 + 稀疏点。

- [ ] **步骤 7：结果记录 + 收尾**

把 `eval_out/report.json` 关键数字与迭代结论追加到规格 §8 下方"实测结果"小节；commit：

```bash
git add docs/superpowers/specs/2026-07-20-surrogate-model-design.md
git commit -m "docs: 代理模型实测结果（任务 14 评估数字）"
```

---

## 自检结果（计划编写后核对）

**规格覆盖度：**
- §5.1 提取流程 → 任务 4/5（复用 nn_export 查询思路与 read_z_mag_db）✓
- §5.2 两级清洗 → 任务 4（clean_records 两级 + physical）✓
- §5.3 确定性位置规则 → 任务 2 ✓
- §5.4 基准表 → 任务 3 ✓
- §5.5 网格划分 → 任务 4（split_cells）✓
- §6 Stage 1 → 任务 7/8 ✓（train/infer 位置一致性：任务 11 两遍前向）
- §7 Stage 2 → 任务 10（从头训练；Step B 作为任务 14 未达标迭代项）✓
- §8 评估（三层指标 + B0/B2/B3 + 分区 + 热图）→ 任务 13 ✓
- §9 工程（魔数入 AlgorithmConfig、pytest、worktree）→ 任务 0/1 + 各任务测试 ✓
- §9 predict CLI → 任务 12 ✓

**已知取舍：**
- 相位通道（§5.1 预留）只做存储（任务 5 的 build_device_record），不进训练 ✓ 符合规格
- 多进程提取直接用 `build_device_record` 提交 ProcessPoolExecutor（顶层函数可 pickle）
- `AlgorithmConfig` 常量一律类属性访问（`AC.surr_k`），与现有代码风格一致
