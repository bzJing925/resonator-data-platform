"""S 参数 / 阻抗曲线计算单元测试。"""

from __future__ import annotations

import numpy as np
import pytest
import skrf

from app.core.curves import PARAM_CHOICES, compute_sparam_curve


def _make_network(n: int = 5) -> skrf.Network:
    """构造一个 1-port 测试网络。"""
    freq = np.linspace(1e9, 3e9, n)
    s = np.zeros((n, 1, 1), dtype=complex)
    z0 = np.full((n, 1), 50.0)
    for i in range(n):
        s[i, 0, 0] = complex(0.5 * np.cos(i * 0.5), 0.3 * np.sin(i * 0.5))
    return skrf.Network(f=freq, s=s, z0=z0)


def test_param_choices() -> None:
    """PARAM_CHOICES 应包含全部 5 种曲线类型。"""
    assert PARAM_CHOICES == ("s11_db", "s11_phase", "s11_re_im", "z_mag_db", "z_phase")


@pytest.mark.parametrize("param", list(PARAM_CHOICES))
def test_compute_sparam_curve_returns_freq(param: str) -> None:
    """所有曲线类型都应返回 freq_ghz。"""
    net = _make_network()
    result = compute_sparam_curve(net, param)  # type: ignore[arg-type]
    assert "freq_ghz" in result
    assert len(result["freq_ghz"]) == net.f.shape[0]


def test_compute_sparam_curve_s11_db() -> None:
    """s11_db 返回 values 且为 dB。"""
    net = _make_network()
    result = compute_sparam_curve(net, "s11_db")
    assert "values" in result
    assert "values_re" not in result
    assert len(result["values"]) == net.f.shape[0]
    # abs(s) <= 1，故 dB <= 0
    assert all(v <= 0 for v in result["values"])


def test_compute_sparam_curve_s11_phase() -> None:
    """s11_phase 返回角度值。"""
    net = _make_network()
    result = compute_sparam_curve(net, "s11_phase")
    assert "values" in result
    assert all(-180 <= v <= 180 for v in result["values"])


def test_compute_sparam_curve_s11_re_im() -> None:
    """s11_re_im 返回 re/im 而不是 values。"""
    net = _make_network()
    result = compute_sparam_curve(net, "s11_re_im")
    assert "values" not in result
    assert "values_re" in result
    assert "values_im" in result
    assert len(result["values_re"]) == net.f.shape[0]
    assert len(result["values_im"]) == net.f.shape[0]


def test_compute_sparam_curve_z_mag_db() -> None:
    """z_mag_db 返回阻抗幅度 dB。"""
    net = _make_network()
    result = compute_sparam_curve(net, "z_mag_db")
    assert "values" in result
    assert len(result["values"]) == net.f.shape[0]


def test_compute_sparam_curve_z_phase() -> None:
    """z_phase 返回阻抗相位。"""
    net = _make_network()
    result = compute_sparam_curve(net, "z_phase")
    assert "values" in result
    assert all(-180 <= v <= 180 for v in result["values"])


def test_compute_sparam_curve_invalid_param() -> None:
    """非法 param 应抛 ValueError。"""
    net = _make_network()
    with pytest.raises(ValueError, match="不支持的曲线类型"):
        compute_sparam_curve(net, "invalid")  # type: ignore[arg-type]
