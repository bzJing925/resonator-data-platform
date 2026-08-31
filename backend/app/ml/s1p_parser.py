"""轻量 S1P Touchstone 文件解析器。

不依赖 skrf，纯 Python 解析 Keysight VNA 导出的 S1P 文件。
支持 RI（实部/虚部）格式，输出 |S|_dB 频谱。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from numpy.typing import NDArray


def parse_s1p(
    path: str | Path,
    target_n_freq: int = 1001,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """解析 S1P 文件，返回频率轴和 S11_dB 频谱。

    步骤：
    1. 跳过注释行（以 ! 开头）
    2. 读取 # 头行，确认格式
    3. 解析数据行 freq Re(S) Im(S)
    4. 计算 |S| = sqrt(Re² + Im²)，再转 dB: 20*log10(|S|)
    5. 线性插值到 target_n_freq 个频点

    Args:
        path: S1P 文件路径。
        target_n_freq: 目标频点数，默认 1001。

    Returns:
        (freq_hz, s11_db) — 均为 shape (target_n_freq,) 的 float64 数组。
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"S1P 文件不存在: {path}")

    freqs: list[float] = []
    re_vals: list[float] = []
    im_vals: list[float] = []

    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            # 跳过注释和头信息行
            if line.startswith("!"):
                continue
            if line.startswith("#"):
                # 格式头，如 "# Hz S RI R 50.0"
                continue
            # 数据行
            parts = line.split()
            if len(parts) < 3:
                continue
            try:
                freq = float(parts[0])
                re_s = float(parts[1])
                im_s = float(parts[2])
                freqs.append(freq)
                re_vals.append(re_s)
                im_vals.append(im_s)
            except ValueError:
                continue

    if len(freqs) < 10:
        raise ValueError(f"{path.name} 解析出的数据点过少（{len(freqs)}）")

    # 计算 |S|_dB
    re_arr = np.array(re_vals, dtype=np.float64)
    im_arr = np.array(im_vals, dtype=np.float64)
    mag = np.sqrt(re_arr**2 + im_arr**2)
    # 避免 log(0)，加极小值
    s11_db = 20.0 * np.log10(np.clip(mag, 1e-12, None))
    freq_arr = np.array(freqs, dtype=np.float64)

    # 线性插值到统一频点数
    if len(freq_arr) != target_n_freq:
        old_x = np.linspace(0.0, 1.0, len(freq_arr))
        new_x = np.linspace(0.0, 1.0, target_n_freq)
        freq_new = np.interp(new_x, old_x, freq_arr)
        s11_new = np.interp(new_x, old_x, s11_db)
        freq_arr = freq_new
        s11_db = s11_new

    return freq_arr, s11_db


def parse_s1p_batch(
    directory: str | Path,
    target_n_freq: int = 1001,
) -> list[dict[str, object]]:
    """批量解析目录下所有 .s1p 文件。

    Args:
        directory: 包含 .s1p 文件的目录。
        target_n_freq: 目标频点数。

    Returns:
        器件列表，每个元素为 dict：
        {
            "filename": str,
            "freq_hz": NDArray,
            "s11_db": NDArray,
        }
    """
    directory = Path(directory)
    files = sorted(directory.glob("*.s1p"))
    results: list[dict[str, object]] = []

    for f in files:
        try:
            freq, s11 = parse_s1p(f, target_n_freq)
            results.append({
                "filename": f.name,
                "freq_hz": freq,
                "s11_db": s11,
            })
        except Exception as exc:
            print(f"[警告] 跳过 {f.name}: {exc}")

    return results
