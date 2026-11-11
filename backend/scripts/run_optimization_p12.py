"""P12: refined/expanded Qp/k2 extrapolation search on frozen artifacts only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ml.optimization_baselines import predict_bundle
from app.ml.optimization_data import sha256, write_json
from app.ml.optimization_p11 import (
    BASELINE_K2,
    BASELINE_QP,
    dominance,
    measured_frontier,
    risk_flags,
)
from app.ml.optimization_p12 import (
    EG_EXTENSION,
    MERGE_SCALED_DISTANCE,
    NEGATIVE_CONTROLS,
    fine_grid,
    grid_argmax,
    mark_catalog_matches,
    pareto_representatives,
    refine_peak_gp,
    scaled_distance,
)

JOINT = ("qp", "k2eff_pct")
MODELS = ("p3", "p6c")


def check_manifest(root, name):
    manifest = json.loads((root / name).read_text())
    files = manifest.get("files", manifest)
    for rel, info in files.items():
        digest = info["sha256"] if isinstance(info, dict) else info
        assert sha256(root / rel) == digest, f"{root.name}/{rel}"
    return manifest


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ["p3-root", "p4-root", "p6c-root", "p11-root", "protocol", "out"]:
        ap.add_argument("--" + name, type=Path, required=True)
    args = ap.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    manifests = {
        "p3": check_manifest(args.p3_root, "receipts/delivery_manifest.json"),
        "p4": check_manifest(args.p4_root, "receipts/delivery_manifest.json"),
        "p6c": check_manifest(args.p6c_root, "delivery_manifest.json"),
        "p11": check_manifest(args.p11_root, "outputs_manifest.json"),
    }
    backend = Path(__file__).resolve().parents[1]
    sources = [
        Path(__file__).resolve(),
        backend / "app/ml/optimization_p12.py",
        backend / "app/ml/optimization_p11.py",
        backend / "app/ml/optimization_baselines.py",
        backend / "app/ml/optimization_data.py",
        args.protocol,
    ]
    hashes = {str(p): sha256(p) for p in sources}
    args.out.mkdir(parents=True)
    write_json(
        args.out / "frozen_inputs.json",
        dict(
            sources=hashes,
            **{
                f"{k}_files": {n: (v["sha256"] if isinstance(v, dict) else v)
                               for n, v in m.get("files", m).items()}
                for k, m in manifests.items()
            },
            simulation_used_as="qualitative_trend_evidence_only_no_qp_priors",
            new_blind_test=False,
        ),
    )
    (args.out / "protocol.md").write_bytes(args.protocol.read_bytes())

    measured = pd.read_csv(args.p4_root / "measured/candidates.csv")
    frontier = measured_frontier(measured)
    frontier.to_csv(args.out / "measured_frontier.csv", index=False)
    for base in (BASELINE_QP, BASELINE_K2):
        row = measured.loc[
            measured.marks.eq(base["marks"])
            & measured.area_um2.eq(base["area_um2"])
            & measured.eg_um.eq(base["eg_um"])
            & measured.fl_um.eq(base["fl_um"])
        ]
        assert len(row) == 1
        base["qp"], base["k2eff_pct"] = float(row.iloc[0].qp), float(row.iloc[0].k2eff_pct)

    grid = fine_grid()
    catalog_mask = mark_catalog_matches(grid, measured)
    grid["in_measured_catalog"] = catalog_mask
    bundle = joblib.load(args.p3_root / "evaluation/scalar_baselines.joblib")
    models = joblib.load(args.p6c_root / "experiment/models.joblib")
    p3 = predict_bundle(bundle, grid.drop(columns=["branch_id", "in_measured_catalog"]))
    for target in JOINT:
        col = p3[p3.target.eq(target)].set_index("input_index")
        grid[f"{target}_p3"] = col.prediction.to_numpy()
        grid[f"{target}_p3_lower"] = col.lower.to_numpy()
        grid[f"{target}_p3_upper"] = col.upper.to_numpy()
        model = models[("structure_holdout", "trend_gp_log_area", target)]
        mean, std = model.predict_log(grid)
        grid[f"{target}_p6c"] = np.exp(mean)
        grid[f"{target}_p6c_log_scale"] = std
    grid.to_csv(args.out / "grid_predictions.csv", index=False)
    # Peak candidates must not coincide with measured structures.
    grid_free = grid[~grid.in_measured_catalog]

    searches = []
    peaks = {}
    for target in JOINT:
        for model in MODELS:
            col = f"{target}_{model}"
            peak = grid_argmax(grid_free, col)
            searches.append(dict(
                target=target, model=model, method="grid_argmax",
                eg_um=peak.eg_um, fl_um=peak.fl_um, value=peak[col],
                above_baseline=bool(
                    peak[col] > (BASELINE_QP["qp"] if target == "qp" else BASELINE_K2["k2eff_pct"])
                ),
            ))
        gp_model = models[("structure_holdout", "trend_gp_log_area", target)]
        starts = [(grid_argmax(grid_free, f"{target}_p6c").eg_um,
                   float(grid_argmax(grid_free, f"{target}_p6c").fl_um)), (1.0, 1.5), (2.0, 0.0)]
        for start in starts:
            eg, fl, value = refine_peak_gp(gp_model.predict_log, start)
            searches.append(dict(
                target=target, model="p6c", method="nelder_mead", start_eg=start[0],
                start_fl=start[1], eg_um=eg, fl_um=fl, log_value=value, value=np.exp(value),
                above_baseline=bool(
                    np.exp(value)
                    > (BASELINE_QP["qp"] if target == "qp" else BASELINE_K2["k2eff_pct"])
                ),
            ))
        # Candidate geometry is the grid argmax, never the continuous refined point:
        # branch-wise models are discontinuous at FL=0 and the GP supremum sits on
        # that branch boundary (FL->0+), which is not a valid candidate geometry.
        peaks[target] = {
            "p3": (float(grid_argmax(grid_free, f"{target}_p3").eg_um),
                   float(grid_argmax(grid_free, f"{target}_p3").fl_um)),
            "p6c": (float(grid_argmax(grid_free, f"{target}_p6c").eg_um),
                    float(grid_argmax(grid_free, f"{target}_p6c").fl_um)),
        }
    reps = {}
    for model in MODELS:
        reps[model] = pareto_representatives(
            grid_free, f"qp_{model}", f"k2eff_pct_{model}", frontier,
            BASELINE_QP["qp"], BASELINE_K2["k2eff_pct"],
        )
        for row in reps[model].itertuples():
            searches.append(dict(
                target="joint", model=model, method="pareto_representative",
                eg_um=row.eg_um, fl_um=row.fl_um,
                qp_value=getattr(row, "qp_" + model), k2_value=getattr(row, "k2eff_pct_" + model),
                frontier_distance=row.frontier_distance,
            ))
    pd.DataFrame(searches).to_csv(args.out / "peak_searches.csv", index=False)

    ext = pd.DataFrame(EG_EXTENSION, columns=["eg_um", "fl_um"])
    ext["area_um2"] = 5500.0
    ext["has_pf"] = 0
    ext["branch_id"] = [
        f"pf0_eg{int(e > 0)}_fl{int(f > 0)}" for e, f in zip(ext.eg_um, ext.fl_um, strict=True)
    ]
    ext_out = predict_bundle(bundle, ext.drop(columns="branch_id"))
    for target in JOINT:
        col = ext_out[ext_out.target.eq(target)].set_index("input_index")
        ext[f"{target}_p3"] = col.prediction.to_numpy()
        model = models[("structure_holdout", "trend_gp_log_area", target)]
        mean, _ = model.predict_log(ext)
        ext[f"{target}_p6c"] = np.exp(mean)
    ext.to_csv(args.out / "eg_extension_behavior.csv", index=False)

    # Candidate geometry set: merged peaks + pareto reps; P11 A/B retained; negative controls.
    points = []
    for target in JOINT:
        p3_pt, gp_pt = peaks[target]["p3"], peaks[target]["p6c"]
        if scaled_distance(p3_pt, gp_pt) < MERGE_SCALED_DISTANCE:
            points.append(dict(eg_um=gp_pt[0], fl_um=gp_pt[1], origin=f"peak_{target}_both"))
        else:
            points.append(dict(eg_um=p3_pt[0], fl_um=p3_pt[1], origin=f"peak_{target}_p3"))
            points.append(dict(eg_um=gp_pt[0], fl_um=gp_pt[1], origin=f"peak_{target}_p6c"))
    seen = {(round(p["eg_um"], 6), round(p["fl_um"], 6)) for p in points}
    for model in MODELS:
        for row in reps[model].itertuples():
            key = (round(row.eg_um, 6), round(row.fl_um, 6))
            if key not in seen:
                seen.add(key)
                points.append(dict(eg_um=row.eg_um, fl_um=row.fl_um, origin=f"joint_{model}"))
    dedup = []
    for p in points:
        key = (round(p["eg_um"], 6), round(p["fl_um"], 6))
        if key not in [(round(d["eg_um"], 6), round(d["fl_um"], 6)) for d in dedup]:
            dedup.append(p)
    new_candidates = pd.DataFrame(dedup)
    new_candidates["area_um2"] = 5500.0
    new_candidates["has_pf"] = 0
    p11 = pd.read_csv(args.p11_root / "candidates.csv")
    p11_ab = p11[p11.group.isin(["A", "B"])].copy()
    p11_ab["origin"] = "P11"
    new_candidates["group"] = "P12"
    p11_ab["group"] = "P11"
    # Cross-group dedup: a P12 peak on an existing P11 geometry merges into the P11 row.
    keep_new = []
    for p in dedup:
        hit = p11_ab[
            np.isclose(p11_ab.eg_um, p["eg_um"], atol=1e-6)
            & np.isclose(p11_ab.fl_um, p["fl_um"], atol=1e-6)
            & np.isclose(p11_ab.area_um2, 5500.0, atol=1e-6)
            & p11_ab.has_pf.eq(0)
        ]
        if len(hit):
            p11_ab.loc[hit.index, "origin"] = "P11+P12:" + p["origin"]
        else:
            keep_new.append(p)
    new_candidates = new_candidates[
        new_candidates.apply(
            lambda r: any(
                r.eg_um == p["eg_um"] and r.fl_um == p["fl_um"] for p in keep_new
            ),
            axis=1,
        )
    ]
    all_points = pd.concat(
        [
            new_candidates[["eg_um", "fl_um", "area_um2", "has_pf", "origin", "group"]],
            p11_ab[["eg_um", "fl_um", "area_um2", "has_pf", "origin", "group"]],
            pd.DataFrame(NEGATIVE_CONTROLS).assign(origin="negative_control", group="C"),
        ],
        ignore_index=True,
    )
    all_points["branch_id"] = [
        f"pf{int(r.has_pf)}_eg{int(r.eg_um > 0)}_fl{int(r.fl_um > 0)}"
        for r in all_points.itertuples()
    ]
    all_points["candidate_id"] = [
        f"{r.group}{i:02d}_{r.origin}_EG{r.eg_um}_FL{r.fl_um}_A{int(r.area_um2)}_PF{r.has_pf}"
        for i, r in enumerate(all_points.itertuples())
    ]

    p3_all = predict_bundle(bundle, all_points.drop(columns=["branch_id"]))
    id_by_index = all_points.candidate_id.reset_index(drop=True)
    p3_all["candidate_id"] = p3_all.input_index.map(id_by_index)
    rows = []
    for point in all_points.itertuples():
        rec = dict(
            candidate_id=point.candidate_id, group=point.group, origin=point.origin,
            eg_um=point.eg_um, fl_um=point.fl_um, area_um2=point.area_um2, has_pf=point.has_pf,
            branch_id=point.branch_id,
        )
        for target in JOINT:
            pr = p3_all[
                p3_all.candidate_id.eq(point.candidate_id) & p3_all.target.eq(target)
            ].iloc[0]
            rec[f"{target}_prediction"] = pr.prediction
            rec[f"{target}_lower"] = pr.lower
            rec[f"{target}_upper"] = pr.upper
            model = models[("structure_holdout", "trend_gp_log_area", target)]
            single = all_points[all_points.candidate_id.eq(point.candidate_id)]
            mean, std = model.predict_log(single)
            rec[f"{target}_p6c"] = float(np.exp(mean[0]))
            rec[f"{target}_p6c_log_scale"] = float(std[0])
        rec["p3_support"] = p3_all[
            p3_all.candidate_id.eq(point.candidate_id) & p3_all.target.eq("qp")
        ].iloc[0].support
        rec["nearest_structure_id"] = p3_all[
            p3_all.candidate_id.eq(point.candidate_id) & p3_all.target.eq("qp")
        ].iloc[0].nearest_structure_id
        rec["nearest_scaled_distance"] = float(p3_all[
            p3_all.candidate_id.eq(point.candidate_id) & p3_all.target.eq("qp")
        ].iloc[0].nearest_scaled_distance)
        rows.append(rec)
    frame = pd.DataFrame(rows)
    wins_d, deltas = [], []
    for row in frame.itertuples():
        flags = risk_flags(row)
        if row.group == "C":
            reason = [c["reason"] for c in NEGATIVE_CONTROLS
                      if c["eg_um"] == row.eg_um and c["fl_um"] == row.fl_um
                      and c["area_um2"] == row.area_um2 and c["has_pf"] == row.has_pf][0]
            flags.append(reason + "——不推荐")
        if row.p3_support != "exact_fitted_structure":
            flags.append(f"支持标签 {row.p3_support}")
        flags.append("k²：仿真 EG↑ 趋势一致（有 3 序列 4 次反例记录）"
                     if row.eg_um > 2.0 else "k²：范围内或插值")
        if row.eg_um >= 3.0 and row.fl_um >= 2.0:
            flags.append("峰位在搜索域双边界角落（EG=3.0 且 FL=2.0），外推可信度最低")
        flags.append("Qp：COMSOL 无电学损耗，无仿真 Qp 先验；仅实测模型")
        flags.append("计算网格，制造版图/步长需设计与工艺确认")
        wins_d.append("；".join(flags))
        wins_p, dom_p = dominance(row.qp_prediction, row.k2eff_pct_prediction, frontier)
        wins_l, _ = dominance(row.qp_lower, row.k2eff_pct_lower, frontier)
        deltas.append(dict(
            qp_delta_vs_qp_baseline=row.qp_prediction - BASELINE_QP["qp"],
            qp_lower_vs_qp_baseline=row.qp_lower - BASELINE_QP["qp"],
            qp_upper_vs_qp_baseline=row.qp_upper - BASELINE_QP["qp"],
            k2_delta_vs_k2_baseline=row.k2eff_pct_prediction - BASELINE_K2["k2eff_pct"],
            k2_lower_vs_k2_baseline=row.k2eff_pct_lower - BASELINE_K2["k2eff_pct"],
            k2_upper_vs_k2_baseline=row.k2eff_pct_upper - BASELINE_K2["k2eff_pct"],
            dominates_frontier_point=";".join(wins_p),
            dominated_by_frontier_point=";".join(dom_p),
            dominates_frontier_lower=";".join(wins_l),
        ))
    frame["risk_flags"] = wins_d
    frame = pd.concat([frame, pd.DataFrame(deltas)], axis=1)
    frame["recommendation"] = [
        "不推荐（负面对照）" if g == "C"
        else ("P12 峰位候选（待实测验证）" if g == "P12" else "P11 保留对照")
        for g in frame["group"]
    ]
    frame.to_csv(args.out / "candidates.csv", index=False)
    pd.DataFrame(NEGATIVE_CONTROLS).assign(note="计算并保留为负面对照，不进图").to_csv(
        args.out / "negative_controls.csv", index=False
    )

    layers = []
    work = frame[frame.group.isin(["P12", "P11"])].copy()
    for caliber, qc, kc in [("point", "qp_prediction", "k2eff_pct_prediction"),
                            ("lower_bound", "qp_lower", "k2eff_pct_lower")]:
        work[f"rank_qp_first_{caliber}"] = (
            work[qc].rank(ascending=False, method="first").astype(int)
        )
        work[f"rank_k2_first_{caliber}"] = (
            work[kc].rank(ascending=False, method="first").astype(int)
        )
    for _, r in work.iterrows():
        layers.append(dict(
            candidate_id=r.candidate_id, group=r.group,
            rank_qp_first_point=int(r.rank_qp_first_point),
            rank_k2_first_point=int(r.rank_k2_first_point),
            rank_qp_first_lower=int(r.rank_qp_first_lower_bound),
            rank_k2_first_lower=int(r.rank_k2_first_lower_bound),
        ))
    pd.DataFrame(layers).to_csv(args.out / "rankings.csv", index=False)

    lines = [
        "# P12 候选最小实测验证方案（冻结模板，更新自 P11）",
        "",
        "测量前冻结：本文件、candidates.csv、peak_searches.csv 与全部预测值/区间。",
        "",
        "1. 版图与工艺确认：候选尺寸为计算网格；节距/形状沿用同系列设计规则。",
        "2. 每候选结构同工艺流片；每结构 ≥12 个坐标位置；同批次测量；original/vz 去嵌。",
        "3. 同批次包含 C6-1 与 E1-1 同结构器件作基线复测对照。",
        "4. 判定：结构典型值落入 P3 区间，且 Qp 或 k² 至少一项超过对应基线"
        "（C6-1 Qp=1395.96 / E1-1 k²=14.6745%）记通过；失败方向降级为开发数据。",
        "5. 建议最小集合：P12 峰位候选全部 + P11 对照中保守下界最接近前沿者；负面对照不进入制造。",
        "",
        "| 候选 | 组 | 来源 | EG | FL | Qp 点预测 [区间] | k² 点预测 [区间] | 推荐级别 |",
        "|---|---|---|---:|---:|---|---|---|",
    ]
    for r in frame.sort_values(["group", "qp_prediction"], ascending=[True, False]).itertuples():
        lines.append(
            f"| {r.candidate_id} | {r.group} | {r.origin} | {r.eg_um} | {r.fl_um} | "
            f"{r.qp_prediction:.1f} [{r.qp_lower:.1f}, {r.qp_upper:.1f}] | "
            f"{r.k2eff_pct_prediction:.3f} [{r.k2eff_pct_lower:.3f}, {r.k2eff_pct_upper:.3f}] | "
            f"{r.recommendation} |"
        )
    (args.out / "falsification_plan.md").write_text("\n".join(lines) + "\n")

    for key, root in [("p3", args.p3_root), ("p4", args.p4_root), ("p6c", args.p6c_root),
                      ("p11", args.p11_root)]:
        files = manifests[key].get("files", manifests[key])
        for rel, info in files.items():
            digest = info["sha256"] if isinstance(info, dict) else info
            assert sha256(root / rel) == digest, rel
    assert all(sha256(Path(p)) == h for p, h in hashes.items())
    write_json(
        args.out / "summary.json",
        dict(
            status="P12_recommendation_candidates_not_verified",
            n_grid_points=len(grid),
            n_candidates=len(frame),
            n_negative_controls=len(NEGATIVE_CONTROLS),
            models_are_frozen_artifacts=True,
            new_blind_test=False,
            production_changed=False,
        ),
    )
    write_json(
        args.out / "outputs_manifest.json",
        {p.name: sha256(p) for p in sorted(args.out.iterdir()) if p.is_file()},
    )
    print(frame[["candidate_id", "group", "qp_prediction", "k2eff_pct_prediction",
                 "recommendation"]].to_string(index=False))


if __name__ == "__main__":
    main()
