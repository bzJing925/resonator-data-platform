"""稀疏采样工具单元测试。

重点验证：
1. 读取 S1P/S2P 后插值到统一频点数。
2. 固定规则采样在 fs/fp 附近更密集。
3. sparse_sampler 不依赖 torch。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import skrf

from app.core.sparse_sampler import (
    fixed_rule_sample,
    partition_regions,
    read_z_mag_db,
    s1p_to_z11_db,
)


def _make_resonator_s1p(path: Path, n: int = 101) -> None:
    """构造一个带串联/并联谐振峰的 1-port S1P 文件。"""
    freq = np.linspace(1.0, 5.0, n)  # GHz
    f0 = 3.0
    # 模拟串联谐振（阻抗最小）和并联谐振（阻抗最大）
    z = 50.0 + 200.0 * ((freq - f0) ** 2 - 0.05) / ((freq - f0) ** 2 + 0.05)
    # 串联谐振处让阻抗接近 0
    z = np.where(np.abs(freq - (f0 - 0.1)) < 0.05, 1.0, z)
    s = (z - 50.0) / (z + 50.0)
    s = np.asarray(s, dtype=complex).reshape(n, 1, 1)
    z0 = np.full((n, 1), 50.0)
    net = skrf.Network(f=freq * 1e9, s=s, z0=z0)
    net.write_touchstone(str(path), form="ma")


def _make_2port_s2p(path: Path, n: int = 101) -> None:
    """构造 S11/S22 不同的 2-port S2P 文件。"""
    freq = np.linspace(1.0, 5.0, n)
    f0 = 3.0
    z1 = 50.0 + 200.0 * ((freq - f0) ** 2 - 0.05) / ((freq - f0) ** 2 + 0.05)
    z2 = 50.0 + 150.0 * ((freq - (f0 + 0.2)) ** 2 - 0.03) / ((freq - (f0 + 0.2)) ** 2 + 0.03)
    s11 = (z1 - 50.0) / (z1 + 50.0)
    s22 = (z2 - 50.0) / (z2 + 50.0)
    s = np.zeros((n, 2, 2), dtype=complex)
    s[:, 0, 0] = s11
    s[:, 1, 1] = s22
    z0 = np.full((n, 2), 50.0)
    net = skrf.Network(f=freq * 1e9, s=s, z0=z0)
    net.write_touchstone(str(path), form="ma")


def test_s1p_to_z11_db_interpolates_to_target_n(tmp_path: Path) -> None:
    path = tmp_path / "test.s1p"
    _make_resonator_s1p(path, n=101)
    freq, z_db = s1p_to_z11_db(path, target_n_freq=1001)
    assert len(freq) == 1001
    assert len(z_db) == 1001
    assert freq.dtype == np.float64
    assert z_db.dtype == np.float64


def test_read_z_mag_db_with_s1p(tmp_path: Path) -> None:
    path = tmp_path / "test.s1p"
    _make_resonator_s1p(path, n=101)
    freq, z_db = read_z_mag_db(path, port="S22", target_n_freq=501)
    # S1P 应忽略 port 参数
    assert len(freq) == 501
    assert len(z_db) == 501


def test_read_z_mag_db_with_s2p_selects_port(tmp_path: Path) -> None:
    path = tmp_path / "test.s2p"
    _make_2port_s2p(path, n=101)
    freq_s11, z_db_s11 = read_z_mag_db(path, port="S11", target_n_freq=101)
    freq_s22, z_db_s22 = read_z_mag_db(path, port="S22", target_n_freq=101)
    np.testing.assert_array_equal(freq_s11, freq_s22)
    # S11 与 S22 的 |Z| dB 应不同
    assert not np.allclose(z_db_s11, z_db_s22)


def test_fixed_rule_sample_denser_near_fs_fp() -> None:
    """构造一个主峰在 3.0 GHz 附近的人造频谱，验证 fs/fp 附近采样更密。"""
    freq = np.linspace(1.0, 5.0, 1001)
    # 在 3.0 附近制造尖锐峰
    z_db = -10.0 * np.exp(-((freq - 3.0) ** 2) / 0.02)
    z_db += 5.0 * np.exp(-((freq - 3.3) ** 2) / 0.02)
    region_mask = partition_regions(z_db, freq)
    sf, sz = fixed_rule_sample(freq, z_db, region_mask, target_k=100, fs=2.9, fp=3.3)

    assert len(sf) > 0
    assert len(sf) == len(sz)
    assert len(sf) <= 100

    # 统计 2.8-3.4 GHz 窗口内的采样点比例，应显著高于该窗口宽度占比
    in_window = np.sum((sf >= 2.8) & (sf <= 3.4))
    window_ratio = in_window / len(sf)
    baseline_ratio = 0.6 / 4.0  # 窗口 0.6 GHz / 总带宽 4 GHz
    assert window_ratio > baseline_ratio * 1.5


def test_sparse_sampler_does_not_import_torch() -> None:
    """训练数据导出路径不能引入 torch（torch 不是核心依赖）。

    子进程隔离验证：全量套件中其他测试会先把 torch 装进 sys.modules，
    直接断言全局 sys.modules 是顺序依赖的误判源。
    """
    import subprocess

    backend_root = Path(__file__).resolve().parents[2]
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import app.core.sparse_sampler; "
            "sys.exit(1 if 'torch' in sys.modules else 0)",
        ],
        cwd=backend_root,
        capture_output=True,
    )
    assert r.returncode == 0, r.stderr.decode()


def test_read_z_mag_db_missing_file_raises(tmp_path: Path) -> None:
    """缺失文件应抛出 FileNotFoundError 或 skrf 异常，供上层跳过。"""
    missing = tmp_path / "not_exist.s1p"
    with pytest.raises((FileNotFoundError, OSError)):
        read_z_mag_db(missing)
