"""设备曲线缓存的内存边界。"""

from __future__ import annotations

from unittest.mock import Mock, patch

import numpy as np

from app.api import devices


def test_bodeq_cache_uses_lightweight_path_key() -> None:
    """缓存不应以频谱 bytes 作为 key，从而避免重复保留大型数组。"""
    network = Mock()
    network.s = np.ones((3, 1, 1), dtype=np.complex128)
    network.f = np.array([1.0, 2.0, 3.0])

    devices._calc_bodeq_cached.cache_clear()
    with (
        patch("app.api.devices._load_network", return_value=network) as load_network,
        patch("app.core.extract.calc_bodeq_curve", return_value={"ok": True}) as calculate,
    ):
        assert devices._calc_bodeq_cached("/data/device.s1p") == {"ok": True}
        assert devices._calc_bodeq_cached("/data/device.s1p") == {"ok": True}

    load_network.assert_called_once_with("/data/device.s1p")
    calculate.assert_called_once()
    spectrum, frequency = calculate.call_args.args
    np.testing.assert_array_equal(spectrum, network.s[:, 0, 0])
    np.testing.assert_array_equal(frequency, network.f)
