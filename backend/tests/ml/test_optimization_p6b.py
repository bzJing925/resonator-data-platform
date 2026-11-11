"""Peak probe guardrails: interpolation is not peak switching or physical mode tracking."""

import numpy as np
import pytest

from app.ml.optimization_peak_diagnostics import direction, refine_vertex, spectrum_probe


def test_vertex_recovers_subsample_peak_without_leaving_neighbors():
    f = np.linspace(5e9, 5.01e9, 11)
    true = f[5] + 2e5
    y = -(((f - true) / 1e6) ** 2)
    refined, ok = refine_vertex(f, y, 5, True)
    assert ok and refined == pytest.approx(true)
    unrefined, ok = refine_vertex(f, y, 5, False)
    assert not ok and unrefined == f[5]
    assert refine_vertex(f, y, 0, True) == (f[0], False)


def test_different_competing_peak_is_diagnostic_not_replacement():
    f = np.linspace(5e9, 5.2e9, 2001)
    db = (
        -20 * np.exp(-(((f - 5.04e9) / 2e6) ** 2))
        + 20 * np.exp(-(((f - 5.12e9) / 2e6) ** 2))
        + 19 * np.exp(-(((f - 5.16e9) / 2e6) ** 2))
    )
    p = spectrum_probe(f, 10 ** (db / 20))
    assert p["fp_hz"] == pytest.approx(5.12e9)
    assert p["fp_competitor_gap_db"] == pytest.approx(1)
    assert len(p["fp_candidates_hz"]) == 2
    assert p["k2_grid_low"] <= p["k2_discrete"] <= p["k2_grid_high"]


def test_nonmonotonic_is_preserved_and_bad_frequency_rejected():
    assert direction([1, 3, 2]) == "nonmonotonic"
    assert direction([1, 1, 2]) == "increasing"
    with pytest.raises(ValueError, match="increasing"):
        spectrum_probe([1, 1, 2], [1, 2, 3])


def test_mixed_sweep_uses_local_resonance_step_not_coarse_outer_step():
    from app.ml.optimization_peak_diagnostics import coupling

    f = np.unique(
        np.concatenate(
            [
                np.arange(5e9, 5.02e9, 2e6),
                np.arange(5.02e9, 5.1801e9, 2e5),
                np.arange(5.182e9, 5.202e9, 2e6),
            ]
        )
    )
    db = -20 * np.exp(-(((f - 5.04e9) / 2e6) ** 2)) + 20 * np.exp(-(((f - 5.12e9) / 2e6) ** 2))
    probe = spectrum_probe(f, 10 ** (db / 20))
    expected = max(
        coupling(probe["fs_hz"] + s * 1e5, probe["fp_hz"] + p * 1e5)
        for s in [-1, 1]
        for p in [-1, 1]
    )
    assert probe["k2_grid_high"] == pytest.approx(expected)
