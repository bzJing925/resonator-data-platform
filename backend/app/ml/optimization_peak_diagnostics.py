"""Read-only peak sensitivity probes. These do not identify physical eigenmodes."""

from __future__ import annotations

import numpy as np
from scipy.signal import find_peaks

from app.core.extract import find_resonances


def refine_vertex(freq, values, index, maximum):
    """Interpolate only the selected extremum; never search for a different peak."""
    if index <= 0 or index >= len(freq) - 1:
        return float(freq[index]), False
    scale = (freq[index + 1] - freq[index - 1]) / 2
    x = (freq[index - 1 : index + 2] - freq[index]) / scale
    a, b, _ = np.polyfit(x, values[index - 1 : index + 2], 2)
    if not np.isfinite(a) or a == 0 or (maximum and a >= 0) or (not maximum and a <= 0):
        return float(freq[index]), False
    vertex = -b / (2 * a)
    if not x[0] <= vertex <= x[-1]:
        return float(freq[index]), False
    return float(freq[index] + vertex * scale), True


def coupling(fs, fp):
    return (np.pi**2 / 4) * (fs / fp) * ((fp - fs) / fp) * 100


def spectrum_probe(freq, z):
    freq, z = np.asarray(freq), np.asarray(z)
    if len(freq) < 3 or len(freq) != len(z) or not np.isfinite(z).all():
        raise ValueError("Finite equal-length spectrum required")
    if not np.isfinite(freq).all() or not (np.diff(freq) > 0).all():
        raise ValueError("Strictly increasing finite frequency required")
    db = 20 * np.log10(np.maximum(np.abs(z), 1e-12))
    fs, fp = find_resonances(np.abs(z), freq)
    rfs, okfs = refine_vertex(freq, db, fs, False)
    rfp, okfp = refine_vertex(freq, db, fp, True)
    peaks, _ = find_peaks(db, prominence=1.0)
    peaks = peaks[(freq[peaks] - freq[fs]) >= 20e6]
    troughs, _ = find_peaks(-db, prominence=1.0)
    others = peaks[peaks != fp]
    trough_others = troughs[troughs != fs]
    df = float(np.max(np.diff(freq)))
    fs_step = float(np.max(np.diff(freq[max(0, fs - 1) : min(len(freq), fs + 2)])))
    fp_step = float(np.max(np.diff(freq[max(0, fp - 1) : min(len(freq), fp + 2)])))
    kcorners = [
        coupling(freq[fs] + s * fs_step / 2, freq[fp] + p * fp_step / 2)
        for s in [-1, 1]
        for p in [-1, 1]
    ]
    return dict(
        fs_hz=float(freq[fs]),
        fp_hz=float(freq[fp]),
        fs_refined_hz=rfs,
        fp_refined_hz=rfp,
        fs_refined_ok=okfs,
        fp_refined_ok=okfp,
        k2_discrete=coupling(freq[fs], freq[fp]),
        k2_refined=coupling(rfs, rfp),
        k2_grid_low=min(kcorners),
        k2_grid_high=max(kcorners),
        frequency_max_step_hz=df,
        fs_step_hz=fs_step,
        fp_step_hz=fp_step,
        uniform_grid=bool(np.allclose(np.diff(freq), df)),
        fp_competitor_gap_db=float(db[fp] - db[others].max()) if len(others) else np.nan,
        fs_competitor_gap_db=float(db[trough_others].min() - db[fs])
        if len(trough_others)
        else np.nan,
        fp_candidates_hz=sorted(set(freq[peaks].tolist() + [float(freq[fp])])),
        fs_candidates_hz=sorted(set(freq[troughs].tolist() + [float(freq[fs])])),
    )


def direction(values):
    values = np.asarray(values)
    delta = np.diff(values)
    tol = max(1e-12, np.max(np.abs(values)) * 1e-9)
    if (np.abs(delta) <= tol).all():
        return "flat"
    if (delta >= -tol).all():
        return "increasing"
    if (delta <= tol).all():
        return "decreasing"
    return "nonmonotonic"
