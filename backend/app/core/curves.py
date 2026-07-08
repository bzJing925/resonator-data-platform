"""S 参数 / 阻抗曲线计算。

纯函数，无 DB/IO 副作用；输入 skrf.Network，输出曲线数据 dict。
"""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
import skrf

CurveParam = Literal["s11_db", "s11_phase", "s11_re_im", "z_mag_db", "z_phase"]
Port = Literal["S11", "S22"]
PARAM_CHOICES: tuple[str, ...] = (
    "s11_db",
    "s11_phase",
    "s11_re_im",
    "z_mag_db",
    "z_phase",
)


def compute_sparam_curve(
    net: skrf.Network,
    param: CurveParam,
    port: Port = "S11",
) -> dict[str, Any]:
    """根据 skrf.Network 计算指定曲线。

    返回 dict 包含：
    - 所有 param：freq_ghz
    - s11_db/s11_phase/z_mag_db/z_phase：values
    - s11_re_im：values_re, values_im
    """
    freq_ghz = (net.f / 1e9).tolist()

    if port == "S11":
        s = net.s[:, 0, 0]
        z0 = net.z0[0, 0]
    elif port == "S22":
        if net.s.shape[1] < 2:
            raise ValueError("S22 需要 2 端口网络")
        s = net.s[:, 1, 1]
        z0 = net.z0[0, 1]
    else:
        raise ValueError(f"不支持的端口: {port}")

    if param == "s11_db":
        values = (20 * np.log10(np.maximum(np.abs(s), 1e-12))).tolist()
        return {"freq_ghz": freq_ghz, "values": values}

    if param == "s11_phase":
        values = [float(v) for v in np.degrees(np.unwrap(np.angle(s)))]
        return {"freq_ghz": freq_ghz, "values": values}

    if param == "s11_re_im":
        return {
            "freq_ghz": freq_ghz,
            "values_re": np.real(s).tolist(),
            "values_im": np.imag(s).tolist(),
        }

    z = z0 * (1 + s) / (1 - s)

    if param == "z_mag_db":
        values = (20 * np.log10(np.maximum(np.abs(z), 1e-12))).tolist()
        return {"freq_ghz": freq_ghz, "values": values}

    if param == "z_phase":
        values = [float(v) for v in np.degrees(np.unwrap(np.angle(z)))]
        return {"freq_ghz": freq_ghz, "values": values}

    raise ValueError(f"不支持的曲线类型: {param}")
