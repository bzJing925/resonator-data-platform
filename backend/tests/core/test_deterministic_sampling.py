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
