"""Trace the systematic ~1e-5 relative gap between recomputed and legacy BodeQ_fitted labels.

Read-only diagnosis on the 289 already-recomputed deembedded curves; no product code changes,
no label edits, no refits of any model. Variants probe optimizer termination, initial values,
fit window, smoothing, group-delay differentiation, and intermediate storage precision.
"""

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import skrf as rf
from scipy.optimize import curve_fit
from scipy.signal import savgol_filter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_algorithm_config
from app.core.extract import _bodeq_raw_array, _interpolate_nan, _lorentzian, _smooth_bodeq
from app.ml.optimization_data import sha256, write_json

FIT_VARIANTS = (
    "baseline",
    "tol_tight",
    "tol_loose",
    "lm_unbounded",
    "x_scale_typical",
    "x_scale_one",
    "jac_3point",
    "p0_perturb",
    "p0_old",
    "window_pm2",
    "window_pm10",
    "window_x0.9",
    "fit_raw",
)
STAGE_VARIANTS = (
    "savgol_w49",
    "savgol_w53",
    "savgol_p2",
    "grad_edge2",
    "quant_12digit",
    "quant_9digit",
    "quant_6digit",
)
CLOUD_MEMBERS = (
    "baseline",
    "tol_tight",
    "tol_loose",
    "lm_unbounded",
    "x_scale_typical",
    "x_scale_one",
    "jac_3point",
    "p0_perturb",
    "p0_old",
)


def curve_name(device_id: str, port: str) -> str:
    stem = re.sub(r"^S(?:11|22)_", "", device_id)
    return re.sub(r"\.s1p$", "", stem) + "_" + port + ".s1p"


def window_of(smooth: np.ndarray, freq: np.ndarray, cfg, shift: int = 0, scale: float = 1.0):
    max_idx = int(np.nanargmax(smooth))
    peak_range = int(len(freq) * cfg.lorentz_peak_range_ratio * scale)
    start = max(0, max_idx - peak_range - shift)
    end = min(len(freq), max_idx + peak_range + shift)
    return max_idx, start, end


def fit_amp(x, y, amp0, f00, g0, variant, old_amp=None):
    p0 = [amp0, f00, g0]
    if variant == "p0_perturb":
        p0 = [amp0 * 1.02, f00 * 1.0005, g0 * 0.9]
    if variant == "p0_old" and old_amp is not None:
        p0 = [old_amp, f00, g0]
    if variant == "lm_unbounded":
        popt, _ = curve_fit(_lorentzian, x, y, p0=p0, method="lm", maxfev=100000)
        return popt
    kw = {}
    if variant == "tol_tight":
        kw = dict(ftol=1e-15, xtol=1e-15, gtol=1e-15, max_nfev=100000)
    if variant == "tol_loose":
        kw = dict(ftol=1e-4, xtol=1e-4, gtol=1e-4)
    if variant == "x_scale_typical":
        kw = dict(x_scale=[amp0, f00, g0])
    if variant == "x_scale_one":
        kw = dict(x_scale=1.0)
    if variant == "jac_3point":
        kw = dict(jac="3-point")
    popt, _ = curve_fit(
        _lorentzian,
        x,
        y,
        p0=p0,
        bounds=([0.1 * amp0, 0.9 * f00, g0 / 10], [10 * amp0, 1.1 * f00, 10 * g0]),
        **kw,
    )
    return popt


def quantize(s: np.ndarray, digits: int) -> np.ndarray:
    def q(x):
        mag = np.abs(x)
        e = np.floor(np.log10(np.where(mag == 0, 1.0, mag))) - digits + 1
        return np.round(x / 10.0**e) * 10.0**e

    return q(s.real) + 1j * q(s.imag)


def bodeq_from(s: np.ndarray, freq: np.ndarray, cfg, stage: str) -> tuple:
    if stage == "grad_edge2":
        omega = 2 * np.pi * freq
        gd = -np.gradient(np.unwrap(np.angle(s)), edge_order=2) / (2 * np.pi * np.gradient(freq))
        mag = np.abs(s)
        den = 1 - mag**2
        raw = np.full_like(den, np.nan, dtype=float)
        mask = den > 1e-6
        raw[mask] = np.abs(omega[mask] * gd[mask] * mag[mask] / den[mask])
        smooth = _smooth_bodeq(raw, freq, cfg)
    elif stage.startswith("savgol_"):
        raw, _ = _bodeq_raw_array(s, freq)
        window = {"savgol_w49": 49, "savgol_w53": 53}.get(stage, 51)
        poly = 2 if stage == "savgol_p2" else cfg.savgol_polyorder
        smooth = savgol_filter(_interpolate_nan(raw, freq), window_length=window, polyorder=poly)
    else:
        raw, _ = _bodeq_raw_array(s, freq)
        smooth = _smooth_bodeq(raw, freq, cfg)
    max_idx, start, end = window_of(smooth, freq, cfg)
    return smooth, max_idx, start, end


def amp_value(popt) -> float:
    """Pipeline reports _lorentzian(f0) evaluated from popt, not raw amp (1e-16 rounding)."""
    return float(_lorentzian(np.asarray([popt[1]]), popt[0], popt[1], popt[2])[0])


def qp_of(s: np.ndarray, cfg) -> float:
    from app.core.extract import calc_q_phase, find_resonances

    z = 50.0 * (1 + s) / (1 - s)
    freq_axis = np.arange(len(z), dtype=float)
    fs_idx, fp_idx = find_resonances(np.abs(z), freq_axis, cfg)
    _, qp = calc_q_phase(z, freq_axis, fs_idx, fp_idx)
    return qp


def analyze(path, cfg, old):
    net = rf.Network(str(path))
    freq, s = net.f, net.s[:, 0, 0]
    out = {}
    smooth, max_idx, start, end = bodeq_from(s, freq, cfg, "default")
    fit_freq, fit_y = freq[start:end], smooth[start:end]
    amp0, f00 = float(np.nanmax(fit_y)), float(freq[max_idx])
    g0 = (float(freq[-1]) - float(freq[0])) / 100.0
    for variant in FIT_VARIANTS:
        try:
            if variant.startswith("window_"):
                shift = {"window_pm2": 2, "window_pm10": 10}.get(variant, 0)
                scale = 0.9 if variant == "window_x0.9" else 1.0
                _, s2, e2 = window_of(smooth, freq, cfg, shift=shift, scale=scale)
                popt = fit_amp(freq[s2:e2], smooth[s2:e2], amp0, f00, g0, "baseline")
            elif variant == "fit_raw":
                raw, _ = _bodeq_raw_array(s, freq)
                raw = _interpolate_nan(raw, freq)
                seg = raw[start:end]
                mask = np.isfinite(seg)
                popt = fit_amp(fit_freq[mask], seg[mask], amp0, f00, g0, "baseline")
            else:
                popt = fit_amp(fit_freq, fit_y, amp0, f00, g0, variant, old_amp=old)
            out[variant] = amp_value(popt)
            if variant == "baseline":
                resid = fit_y - _lorentzian(fit_freq, *popt)
                out["resid_rms_norm"] = float(np.sqrt(np.mean(resid**2)) / popt[0])
                out["ssr_baseline"] = float(np.sum(resid**2))
                out["gamma_mhz"] = float(popt[2]) / 1e6
                out["f0_minus_guess_mhz"] = (float(popt[1]) - f00) / 1e6
                out["fit_window_n"] = int(end - start)
        except Exception:
            out[variant] = float("nan")
    try:
        popt_fix, _ = curve_fit(
            lambda f, f0, g: _lorentzian(f, old, f0, g),
            fit_freq,
            fit_y,
            p0=[f00, g0],
            bounds=([0.9 * f00, g0 / 10], [1.1 * f00, 10 * g0]),
        )
        resid_fix = fit_y - _lorentzian(fit_freq, old, *popt_fix)
        out["ssr_amp_fixed_old_ratio"] = float(np.sum(resid_fix**2) / out["ssr_baseline"])
    except Exception:
        out["ssr_amp_fixed_old_ratio"] = float("nan")
    for stage in STAGE_VARIANTS:
        try:
            sv = s
            if stage.startswith("quant"):
                sv = quantize(s, int(re.search(r"\d+", stage).group()))
            sm2, _, s3, e3 = bodeq_from(sv, freq, cfg, stage)
            popt = fit_amp(
                freq[s3:e3], sm2[s3:e3], float(np.nanmax(sm2[s3:e3])), f00, g0, "baseline"
            )
            out[stage] = amp_value(popt)
        except Exception:
            out[stage] = float("nan")
    try:
        out["qp_baseline"] = qp_of(s, cfg)
        out["qp_quant_9digit"] = qp_of(quantize(s, 9), cfg)
        out["qp_quant_6digit"] = qp_of(quantize(s, 6), cfg)
    except Exception:
        out["qp_baseline"] = out["qp_quant_9digit"] = out["qp_quant_6digit"] = float("nan")
    return out


def run(p6g, verify900, out):
    out.mkdir(parents=True, exist_ok=False)
    cand_path = p6g / "audit/candidate_results.csv"
    v900_path = verify900 / "verify_results.csv"
    inputs = {str(p.resolve()): sha256(p) for p in [cand_path, v900_path]}
    cand = pd.read_csv(cand_path)
    cand = cand.loc[cand.method.eq("original") & cand.order.eq("archive")]
    g700 = pd.DataFrame(
        dict(
            device_id=cand.device_id,
            group=700,
            curve=[
                p6g / "audit/curves/original_archive" / curve_name(d, "S11")
                for d in cand.device_id
            ],
            old=cand.old_bodeq_fitted,
            stored_new=cand.bodeq_fitted,
        )
    )
    v9 = pd.read_csv(v900_path)
    g900 = pd.DataFrame(
        dict(
            device_id=v9.device_id,
            group=900,
            curve=[verify900 / "curves" / d for d in v9.dut_file],
            old=v9.old_bodeq_fitted,
            stored_new=v9.bodeq_fitted,
        )
    )
    frame = pd.concat([g700, g900], ignore_index=True)
    assert len(frame) == 289 and frame.curve.map(lambda p: Path(p).is_file()).all()
    manifest = pd.DataFrame(
        dict(file=[str(Path(p).name) for p in frame.curve], sha256=frame.curve.map(sha256))
    )
    manifest.to_csv(out / "curves_manifest.csv", index=False)

    cfg = get_algorithm_config()
    rows = []
    for item in frame.itertuples():
        rec = dict(
            device_id=item.device_id, group=item.group, old=item.old, stored_new=item.stored_new
        )
        rec.update(analyze(Path(item.curve), cfg, item.old))
        rec["baseline_exact_match"] = bool(rec["baseline"] == item.stored_new)
        cloud = [rec[k] for k in CLOUD_MEMBERS if np.isfinite(rec[k])]
        rec["cloud_min"], rec["cloud_max"] = min(cloud), max(cloud)
        rec["old_in_cloud"] = bool(rec["cloud_min"] <= item.old <= rec["cloud_max"])
        rows.append(rec)
    df = pd.DataFrame(rows)
    df.to_csv(out / "per_record_variants.csv", index=False)

    variants = list(FIT_VARIANTS) + list(STAGE_VARIANTS)
    summary = []
    for v in variants:
        d_old = df[v] - df.old
        d_base = df[v] - df.baseline
        rel_old = d_old.abs() / df.old.abs()
        summary.append(
            dict(
                variant=v,
                n_finite=int(np.isfinite(df[v]).sum()),
                mae_vs_old=d_old.abs().mean(),
                max_abs_vs_old=d_old.abs().max(),
                max_rel_vs_old=rel_old.max(),
                mean_signed_rel_vs_old=(d_old / df.old).mean(),
                median_abs_rel_shift_vs_baseline=(d_base.abs() / df.baseline.abs()).median(),
                max_abs_rel_shift_vs_baseline=(d_base.abs() / df.baseline.abs()).max(),
                closer_to_old_count=int((d_old.abs() < (df.baseline - df.old).abs()).sum()),
                same_sign_as_gap_count=int((np.sign(d_old) == np.sign(df.baseline - df.old)).sum()),
            )
        )
    gap = df.baseline - df.old
    gap_rel = gap.abs() / df.old.abs()
    g7 = df.loc[df.group.eq(700)]
    g9 = df.loc[df.group.eq(900)]
    extra = dict(
        baseline_gap_mean_signed_rel=(gap / df.old).mean(),
        baseline_gap_max_rel=gap_rel.max(),
        baseline_gap_median_rel=gap_rel.median(),
        baseline_gap_sign_pos=int((gap > 0).sum()),
        baseline_gap_sign_neg=int((gap < 0).sum()),
        gap_median_rel_group700=((g7.baseline - g7.old).abs() / g7.old.abs()).median(),
        gap_max_rel_group700=((g7.baseline - g7.old).abs() / g7.old.abs()).max(),
        gap_median_rel_group900=((g9.baseline - g9.old).abs() / g9.old.abs()).median(),
        gap_max_rel_group900=((g9.baseline - g9.old).abs() / g9.old.abs()).max(),
        baseline_exact_match_count=int(df.baseline_exact_match.sum()),
        old_in_cloud_count=int(df.old_in_cloud.sum()),
        p0_old_shift_median_rel=((df.p0_old - df.baseline).abs() / df.baseline.abs()).median(),
        p0_old_shift_max_rel=((df.p0_old - df.baseline).abs() / df.baseline.abs()).max(),
        ssr_amp_fixed_old_ratio_median=df.ssr_amp_fixed_old_ratio.median(),
        ssr_amp_fixed_old_ratio_max=df.ssr_amp_fixed_old_ratio.max(),
        qp_quant_9digit_max_abs_shift=(df.qp_quant_9digit - df.qp_baseline).abs().max(),
        qp_quant_6digit_max_abs_shift=(df.qp_quant_6digit - df.qp_baseline).abs().max(),
        corr_absrel_gap_vs_resid_rms=np.corrcoef(gap_rel, df.resid_rms_norm)[0, 1],
        corr_absrel_gap_vs_tol_tight_shift=np.corrcoef(
            gap_rel,
            (df.tol_tight - df.baseline).abs() / df.baseline.abs(),
        )[0, 1],
        corr_absrel_gap_vs_quant9_shift=np.corrcoef(
            gap_rel,
            (df.quant_9digit - df.baseline).abs() / df.baseline.abs(),
        )[0, 1],
    )
    summary.append(dict(variant="__gap_diagnostics__", **{k: float(v) for k, v in extra.items()}))
    pd.DataFrame(summary).to_csv(out / "variant_summary.csv", index=False)

    verification = verify(out, df)
    write_json(out / "verification.json", verification)
    assert all(sha256(Path(p)) == h for p, h in inputs.items())
    write_json(
        out / "receipt.json",
        dict(
            inputs=inputs,
            curves_manifest_sha256=sha256(out / "curves_manifest.csv"),
            n_curves=len(manifest),
            new_fit=False,
            labels_changed=False,
            diagnosis_only=True,
            code={
                str(p): sha256(p)
                for p in [Path(__file__), Path("app/core/extract.py"), Path("app/config.py")]
            },
            files={p.name: sha256(p) for p in sorted(out.glob("*.csv"))},
        ),
    )
    print(pd.DataFrame(summary).to_string(index=False))
    return df


def verify(out, df):
    """csv-module recheck of headline numbers, independent of the pandas path."""
    import csv

    with (out / "per_record_variants.csv").open() as f:
        rows = list(csv.DictReader(f))
    gap_rel = [abs(float(r["baseline"]) - float(r["old"])) / abs(float(r["old"])) for r in rows]
    tight_shift = [
        abs(float(r["tol_tight"]) - float(r["baseline"])) / abs(float(r["baseline"]))
        for r in rows
    ]
    exact = sum(1 for r in rows if r["baseline"] == r["stored_new"])
    cloud = sum(
        1 for r in rows if float(r["cloud_min"]) <= float(r["old"]) <= float(r["cloud_max"])
    )
    return {
        "n_records": len(rows),
        "baseline_equals_stored_new": f"{exact}/{len(rows)}",
        "old_inside_optimizer_cloud": f"{cloud}/{len(rows)}",
        "gap_median_rel": float(np.median(gap_rel)),
        "gap_max_rel": max(gap_rel),
        "tol_tight_shift_median_rel": float(np.median(tight_shift)),
        "tol_tight_shift_max_rel": max(tight_shift),
        "matches_pandas_path": bool(
            exact == int(df.baseline_exact_match.sum())
            and cloud == int(df.old_in_cloud.sum())
            and np.isclose(max(gap_rel), float(((df.baseline - df.old).abs() / df.old.abs()).max()))
        ),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ["p6g", "verify900", "out"]:
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    run(args.p6g, args.verify900, args.out)
