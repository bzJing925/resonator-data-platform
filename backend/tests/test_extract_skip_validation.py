"""Pydantic skip_validation 路径单元测试。

覆盖 extract_resonator_params 的 skip_validation=True/False 分支，
确保 Worker 批量场景能拿到 dict、跳过大模型校验。
"""

from __future__ import annotations

import numpy as np
import pytest

from app.core.extract import ExtractError, extract_resonator_params
from app.schemas.resonator import ResonatorRow


def _write_synthetic_s1p(path, freq, s_complex):
    """写一个最简 s1p Touchstone 文件，让 skrf.Network 能加载。"""
    with open(path, "w") as f:
        f.write("# Hz S MA R 50\n")
        for fi, si in zip(freq, s_complex, strict=True):
            mag = float(abs(si))
            phase_deg = float(np.degrees(np.angle(si)))
            f.write(f"{fi:.6e} {mag:.6e} {phase_deg:.6e}\n")


def _synth_resonator_z(freq: np.ndarray, fs: float, fp: float) -> np.ndarray:
    """构造谐振器阻抗谱：fs 处低谷、fp 处高峰。"""
    z = 50 * np.ones_like(freq, dtype=float)
    sigma = (freq[-1] - freq[0]) * 0.005
    z -= 49 * np.exp(-((freq - fs) ** 2) / (2 * sigma**2))
    z += 950 * np.exp(-((freq - fp) ** 2) / (2 * sigma**2))
    return z


def _make_good_s1p(path: str) -> None:
    """写一个能通过完整提取流程的合法 s1p 文件。"""
    freq = np.linspace(1e9, 3e9, 801)
    z = _synth_resonator_z(freq, fs=1.8e9, fp=2.2e9)
    # z → s：s = (z - 50) / (z + 50)
    s = (z - 50.0) / (z + 50.0 + 0j)
    _write_synthetic_s1p(path, freq, s)


# ── skip_validation=True 返回 dict ───────────────────────────────────────


def test_skip_validation_returns_dict(tmp_path) -> None:
    """skip_validation=True → 返回 dict 而非 ResonatorRow。"""
    p = tmp_path / "good.s1p"
    _make_good_s1p(str(p))
    result = extract_resonator_params(str(p), skip_validation=True)
    assert isinstance(result, dict)
    assert not isinstance(result, ResonatorRow)


def test_skip_validation_dict_has_all_fields(tmp_path) -> None:
    """返回的 dict 应包含全部 24+ 字段。"""
    p = tmp_path / "good.s1p"
    _make_good_s1p(str(p))
    result = extract_resonator_params(str(p), skip_validation=True)
    assert isinstance(result, dict)
    expected_keys = {
        "original_filename",
        "display_name",
        "folder_name",
        "s_param_path",
        "wafer",
        "coord",
        "x",
        "y",
        "mark",
        "eg",
        "fl",
        "ag",
        "pf",
        "area_n",
        "area_um2",
        "fs_ghz",
        "fp_ghz",
        "zs_ohm",
        "zp_ohm",
        "qs",
        "qp",
        "qs_bodeq",
        "qp_bodeq",
        "dbqs",
        "dbqp",
        "bodeq_fitted",
        "bodeq_smooth",
        "bodeq_raw",
        "fbode_ghz",
        "k2eff_pct",
        "fp2_ghz",
        "fs2_ghz",
        "zp2_ohm",
        "zs2_ohm",
        "deembedded",
    }
    assert expected_keys <= set(result.keys())


def test_skip_validation_true_numeric_fields_present(tmp_path) -> None:
    """核心数值字段在 dict 中应为有效浮点数。"""
    p = tmp_path / "good.s1p"
    _make_good_s1p(str(p))
    result = extract_resonator_params(str(p), skip_validation=True)
    assert isinstance(result["fs_ghz"], float)
    assert isinstance(result["fp_ghz"], float)
    assert result["fs_ghz"] < result["fp_ghz"]
    assert result["deembedded"] is False


# ── skip_validation=False 返回 ResonatorRow ──────────────────────────────


def test_skip_validation_false_returns_row(tmp_path) -> None:
    """skip_validation=False（默认）→ 返回 ResonatorRow 实例。"""
    p = tmp_path / "good.s1p"
    _make_good_s1p(str(p))
    result = extract_resonator_params(str(p), skip_validation=False)
    assert isinstance(result, ResonatorRow)


def test_skip_validation_default_is_false(tmp_path) -> None:
    """不显式传 skip_validation → 默认 False，返回 ResonatorRow。"""
    p = tmp_path / "good.s1p"
    _make_good_s1p(str(p))
    result = extract_resonator_params(str(p))
    assert isinstance(result, ResonatorRow)


# ── 异常路径不受 skip_validation 影响 ─────────────────────────────────────


def test_skip_validation_true_still_raises_on_bad_file(tmp_path) -> None:
    """skip_validation=True 不能掩盖文件加载失败。"""
    p = tmp_path / "bad.s1p"
    p.write_text("not a touchstone file")
    with pytest.raises(ExtractError):
        extract_resonator_params(str(p), skip_validation=True)


def test_skip_validation_true_still_raises_on_fs_ge_fp(tmp_path) -> None:
    """skip_validation=True 不能掩盖 fs >= fp 的异常。"""
    p = tmp_path / "degenerate.s1p"
    freq = np.linspace(1e9, 2e9, 500)
    # z 严格单调递减 → fs 在末尾、fp 在开头
    z = 1000 - 950 * (freq - freq[0]) / (freq[-1] - freq[0])
    s = (z - 50.0) / (z + 50.0 + 0j)
    _write_synthetic_s1p(p, freq, s)
    with pytest.raises(ExtractError):
        extract_resonator_params(str(p), skip_validation=True)


# ── API 契约 ─────────────────────────────────────────────────────────────


def test_extract_signature_accepts_skip_validation() -> None:
    """extract_resonator_params 必须接受 skip_validation 参数（防签名回归）。"""
    import inspect

    sig = inspect.signature(extract_resonator_params)
    assert "skip_validation" in sig.parameters
    param = sig.parameters["skip_validation"]
    assert param.default is False
