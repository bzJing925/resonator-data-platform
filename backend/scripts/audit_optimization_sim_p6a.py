"""Separate simulation-only trend audit; never reads measured labels or trains a model."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import AlgorithmConfig
from app.core.extract import extract_resonator_params
from app.ml.optimization_data import sha256, write_json

AXES = [
    "Train_A_res_um2",
    "Train_L_FL_um",
    "Train_h_FL_nm",
    "Train_L_EG_um",
    "Train_h_EG_nm",
    "Train_L_AG_um",
    "Train_h_AG_nm",
]
CONTROLS = [
    "Stage",
    "Template",
    "Variant",
    "EG_State",
    "has_FL",
    "has_AG",
    "has_patterned_EG",
    "EG_material_present",
    "has_PF",
    "is_BASIC",
    "is_C_gt_B",
    "Train_PF_nm",
    "Air_margin_per_side_um",
    "L_air_Rule",
    "frequency_min_ghz",
    "frequency_max_ghz",
]
METRICS = ["fs_ghz", "fp_ghz", "k2eff_pct", "qs", "qp", "bodeq_fitted"]


def extract_one(item):
    row_id, path = item
    result = {"Training_Row_ID": row_id}
    try:
        import skrf as rf

        path = Path(path)
        result["source_sha256"] = sha256(path)
        network = rf.Network(str(path))
        freq, s = network.f, network.s[:, 0, 0]
        result.update(
            n_frequency_points=len(freq),
            frequency_min_ghz=round(float(freq.min() / 1e9), 6),
            frequency_max_ghz=round(float(freq.max() / 1e9), 6),
            max_s11_magnitude=float(np.abs(s).max()),
        )
        if (
            network.nports != 1
            or len(freq) < 3
            or not np.isfinite(freq).all()
            or not np.isfinite(s).all()
            or not (np.diff(freq) > 0).all()
        ):
            raise ValueError("Nonfinite, non-increasing or invalid one-port sweep")
        extracted = extract_resonator_params(path, config=AlgorithmConfig(), skip_validation=True)
        result.update({k: extracted.get(k) for k in [*METRICS, "zs_ohm", "zp_ohm"]})
        values = np.asarray([result[k] for k in METRICS], dtype=float)
        if not np.isfinite(values).all() or (values <= 0).any():
            raise ValueError("Missing, nonpositive or nonfinite extracted metric")
        if (
            not freq.min() / 1e9 < result["fs_ghz"] < result["fp_ghz"] < freq.max() / 1e9
            or result["k2eff_pct"] >= 100
        ):
            raise ValueError("Invalid resonance order, edge resonance or k2")
        result["extraction_status"] = "ok"
        result["passivity_flag"] = bool(result["max_s11_magnitude"] > 1 + 1e-6)
    except Exception as exc:
        result["extraction_status"] = f"{type(exc).__name__}: {exc}"
    return result


def prepare_geometry(frame):
    frame = frame.copy()
    # Absent PF is explicit in this map. Present PF with unknown thickness stays unknown.
    frame.loc[frame.has_PF.eq(0) & frame.Train_PF_nm.isna(), "Train_PF_nm"] = 0.0
    reasons = []
    for _, row in frame.iterrows():
        flags = []
        if row[AXES + CONTROLS].isna().any():
            flags.append("incomplete_physical_or_topology_metadata")
        for key in AXES:
            snp_key = key.replace("Train_", "SNP_")
            if pd.isna(row[snp_key]) or not np.isclose(row[key], row[snp_key]):
                flags.append(f"train_snp_mismatch:{key}")
        if not np.isclose(row.Train_L_bot_um - row.Train_L_air_um, 6):
            flags.append("air_rule_mismatch")
        if row.extraction_status != "ok":
            flags.append("extraction_failure")
        if row.get("passivity_flag", False) == True:  # noqa: E712
            flags.append("s11_passivity_flag")
        reasons.append(";".join(flags))
    frame["exclusion_reason"] = reasons
    frame["trend_eligible"] = frame.exclusion_reason.eq("")
    return frame


def trend_tables(frame):
    summaries, pairs, rejected = [], [], []
    for cohort, source in [
        ("all_audited", frame),
        ("consistent_only", frame[frame.Cross_Source_Status.eq("CONSISTENT")]),
    ]:
        eligible = source[source.trend_eligible]
        for axis in AXES:
            controls = [*CONTROLS, *(a for a in AXES if a != axis)]
            if axis != "Train_A_res_um2":
                controls += ["Train_L_top_um", "Train_L_bot_um", "Train_L_air_um"]
            for key, group in eligible.groupby(controls, dropna=False):
                if group[axis].nunique() < 3:
                    continue
                control_values = dict(zip(controls, key, strict=True))
                series = hashlib.sha256(
                    json.dumps([cohort, axis, control_values], sort_keys=True).encode()
                ).hexdigest()[:16]
                if group[axis].duplicated().any():
                    rejected.append(
                        dict(
                            cohort=cohort,
                            axis=axis,
                            series_id=series,
                            reason="duplicate_axis_level_no_aggregation",
                            row_ids=";".join(group.Training_Row_ID),
                        )
                    )
                    continue
                group = group.sort_values(axis)
                for metric in METRICS:
                    values = group[metric].to_numpy(float)
                    delta = np.diff(values)
                    tolerance = max(1e-12, np.max(np.abs(values)) * 1e-9)
                    signs = np.sign(np.where(np.abs(delta) <= tolerance, 0, delta))
                    direction = (
                        "increasing"
                        if (signs >= 0).all() and (signs > 0).any()
                        else "decreasing"
                        if (signs <= 0).all() and (signs < 0).any()
                        else "flat"
                        if (signs == 0).all()
                        else "nonmonotonic"
                    )
                    summaries.append(
                        dict(
                            cohort=cohort,
                            axis=axis,
                            metric=metric,
                            series_id=series,
                            n_levels=len(group),
                            direction=direction,
                            axis_min=group[axis].min(),
                            axis_max=group[axis].max(),
                            relative_range=(values.max() - values.min()) / np.median(values),
                            controls=json.dumps(control_values, sort_keys=True),
                            row_ids=";".join(group.Training_Row_ID),
                            transfer_status="simulation_only_not_validated_on_measurement",
                        )
                    )
                    for i in range(len(group) - 1):
                        lo, hi = group.iloc[i], group.iloc[i + 1]
                        pairs.append(
                            dict(
                                cohort=cohort,
                                axis=axis,
                                metric=metric,
                                series_id=series,
                                low_id=lo.Training_Row_ID,
                                high_id=hi.Training_Row_ID,
                                low_axis=lo[axis],
                                high_axis=hi[axis],
                                low_value=values[i],
                                high_value=values[i + 1],
                                delta=delta[i],
                                ratio=values[i + 1] / values[i],
                            )
                        )
    return pd.DataFrame(summaries), pd.DataFrame(pairs), pd.DataFrame(rejected)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snp-root", type=Path, required=True)
    ap.add_argument("--map", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    mapping = pd.read_excel(args.map, sheet_name="Training_Index")
    matched = mapping[mapping.SNP_Status.eq("MATCHED")].copy()
    if matched.SNP_Relative_Path.duplicated().any() or matched.Training_Row_ID.duplicated().any():
        raise ValueError("Mapping must identify unique full paths and rows")
    matched["resolved_path"] = [
        str(args.snp_root / str(p).replace("\\", "/")) for p in matched.SNP_Relative_Path
    ]
    if not all(Path(p).is_file() for p in matched.resolved_path):
        raise ValueError("Missing mapped files")
    args.out.mkdir(parents=True)
    backend = Path(__file__).resolve().parents[1]
    sources = [
        args.map,
        Path(__file__).resolve(),
        *sorted((backend / "app/core").glob("*.py")),
        backend / "app/config.py",
        backend / "app/ml/optimization_data.py",
    ]
    hashes = {str(p): sha256(p) for p in sources}
    write_json(
        args.out / "frozen_inputs.json",
        dict(
            sources=hashes,
            extraction_window="full_native_sweep; equal sweep bounds within each trend series",
            algorithm_config=asdict(AlgorithmConfig()),
            versions={
                name: importlib.metadata.version(name)
                for name in ["numpy", "pandas", "scipy", "scikit-rf"]
            },
            no_electrical_loss=True,
            measured_labels_read=False,
            raw_de_mix=False,
            mode_tracking_verified=False,
            intended_use="simulation_only_trend_hypotheses",
        ),
    )
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        results = list(
            pool.map(extract_one, zip(matched.Training_Row_ID, matched.resolved_path, strict=True))
        )
    frame = prepare_geometry(
        matched.merge(pd.DataFrame(results), on="Training_Row_ID", validate="one_to_one")
    )
    frame.to_csv(args.out / "simulation_extraction.csv", index=False)
    frame[~frame.trend_eligible].to_csv(args.out / "quarantine.csv", index=False)
    mapping[~mapping.SNP_Status.eq("MATCHED")].to_csv(
        args.out / "unmatched_map_rows.csv", index=False
    )
    trends, pairs, rejected = trend_tables(frame)
    trends.to_csv(args.out / "trend_series.csv", index=False)
    pairs.to_csv(args.out / "trend_pairs.csv", index=False)
    rejected.to_csv(args.out / "rejected_series.csv", index=False)
    directions = (
        (trends.groupby(["cohort", "axis", "metric", "direction"]).size().unstack(fill_value=0))
        if not trends.empty
        else pd.DataFrame()
    )
    directions.to_csv(args.out / "trend_directions.csv")
    expected = {Path(p).resolve() for p in matched.resolved_path}
    actual = {p.resolve() for p in args.snp_root.rglob("*") if p.suffix.lower() == ".s1p"}
    summary = dict(
        n_map=len(mapping),
        n_matched=len(matched),
        n_files=len(actual),
        n_orphan_files=len(actual - expected),
        extraction_status=frame.extraction_status.value_counts().to_dict(),
        trend_eligible=int(frame.trend_eligible.sum()),
        exclusions=frame.exclusion_reason.value_counts().to_dict(),
        n_series=len(trends),
        n_pairs=len(pairs),
        n_rejected_series=len(rejected),
        simulation_labels_used_for_measured_training=False,
        mode_tracking_verified=False,
        new_measurements_available=False,
    )
    if any(sha256(Path(p)) != h for p, h in hashes.items()):
        raise RuntimeError("Source changed during extraction")
    if any(sha256(Path(r.resolved_path)) != r.source_sha256 for r in frame.itertuples()):
        raise RuntimeError("Simulation changed during extraction")
    write_json(args.out / "summary.json", summary)
    write_json(
        args.out / "outputs_manifest.json",
        {p.name: sha256(p) for p in args.out.iterdir() if p.is_file()},
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
