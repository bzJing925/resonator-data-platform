"""P11: joint Qp/k2 extrapolation candidate recommendation on frozen artifacts only."""

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
    EXCLUSIONS,
    JOINT_TARGETS,
    build_candidates,
    dominance,
    measured_frontier,
    pareto_layer,
    risk_flags,
)

TARGET_LABEL = {"qp": "Qp", "k2eff_pct": "k2eff_pct"}


def check_manifest(root, name):
    manifest = json.loads((root / name).read_text())
    files = manifest.get("files", manifest)
    for rel, info in files.items():
        digest = info["sha256"] if isinstance(info, dict) else info
        assert sha256(root / rel) == digest, f"{root.name}/{rel}"
    return manifest


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ["p3-root", "p4-root", "p6a-root", "p6c-root", "protocol", "out"]:
        ap.add_argument("--" + name, type=Path, required=True)
    args = ap.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    manifests = {
        "p3": check_manifest(args.p3_root, "receipts/delivery_manifest.json"),
        "p4": check_manifest(args.p4_root, "receipts/delivery_manifest.json"),
        "p6c": check_manifest(args.p6c_root, "delivery_manifest.json"),
    }
    sim_evidence = args.p6a_root / "simulation_audit_v2/trend_directions.csv"
    backend = Path(__file__).resolve().parents[1]
    sources = [
        Path(__file__).resolve(),
        backend / "app/ml/optimization_p11.py",
        backend / "app/ml/optimization_baselines.py",
        backend / "app/ml/optimization_data.py",
        args.protocol,
    ]
    hashes = {str(p): sha256(p) for p in sources}
    hashes[str(sim_evidence.resolve())] = sha256(sim_evidence)
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
        assert len(row) == 1, base["marks"]
        base["qp"] = float(row.iloc[0].qp)
        base["k2eff_pct"] = float(row.iloc[0].k2eff_pct)
    champ_qp = measured[measured.marks.eq("C6-1") & measured.area_um2.eq(5500.0)].iloc[0]
    champ_k2 = measured[measured.marks.eq("E1-1") & measured.area_um2.eq(5500.0)].iloc[0]
    assert np.isclose(champ_qp.qp, 1395.957083)  # Frozen champions re-derived.
    assert np.isclose(champ_k2.k2eff_pct, 14.674470)

    candidates = build_candidates()
    bundle = joblib.load(args.p3_root / "evaluation/scalar_baselines.joblib")
    p3 = predict_bundle(bundle, candidates)
    id_by_index = candidates.candidate_id.reset_index(drop=True)
    p3["candidate_id"] = p3.input_index.map(id_by_index)
    models = joblib.load(args.p6c_root / "experiment/models.joblib")
    sens_rows = []
    for target in JOINT_TARGETS:
        model = models[("structure_holdout", "trend_gp_log_area", target)]
        mean, std = model.predict_log(candidates)
        sens_rows.append(pd.DataFrame(
            dict(candidate_id=candidates.candidate_id, target=target,
                 p6c_prediction=np.exp(mean), p6c_log_scale=std)
        ))
    sens = pd.concat(sens_rows, ignore_index=True)

    wide = p3[p3.target.isin(JOINT_TARGETS)].pivot_table(
        index="candidate_id", values=["prediction", "lower", "upper", "support"],
        columns="target", aggfunc="first",
    )
    wide.columns = [f"{t}_{m}" for m, t in wide.columns]
    frame = candidates.merge(wide, on="candidate_id").merge(
        sens[sens.target.eq("qp")][["candidate_id", "p6c_prediction", "p6c_log_scale"]]
        .rename(columns={"p6c_prediction": "p6c_qp", "p6c_log_scale": "p6c_qp_log_scale"}),
        on="candidate_id",
    ).merge(
        sens[sens.target.eq("k2eff_pct")][["candidate_id", "p6c_prediction"]]
        .rename(columns={"p6c_prediction": "p6c_k2eff_pct"}), on="candidate_id",
    )
    support_cols = p3[p3.target.eq("qp")][
        ["candidate_id", "support", "nearest_structure_id", "nearest_scaled_distance"]
    ].rename(columns={"support": "p3_support"})
    frame = frame.merge(support_cols, on="candidate_id")

    degenerate = (
        (frame.k2eff_pct_prediction >= 100)
        | (frame.qp_prediction <= 0)
        | ~np.isfinite(frame.qp_prediction)
        | ~np.isfinite(frame.k2eff_pct_prediction)
    )
    excluded = [dict(direction=d, reason=r) for d, r in EXCLUSIONS]
    for row in frame[degenerate].itertuples():
        excluded.append(
            dict(direction=row.candidate_id, reason="非物理或退化预测（k²≥100/非正值/区间缺失）")
        )
    pd.DataFrame(excluded).to_csv(args.out / "excluded.csv", index=False)
    frame = frame[~degenerate].reset_index(drop=True)

    risks, deltas = [], []
    for row in frame.itertuples():
        flags = risk_flags(row)
        if row.p3_support != "exact_fitted_structure":
            flags.append(f"支持标签 {row.p3_support}")
        flags.append("k²：仿真 EG↑ 趋势一致（有 3 序列 4 次反例记录）" if row.group == "A"
                     else "k²：凸包内插值，无仿真方向性依赖")
        if row.group != "C":
            flags.append("Qp：COMSOL 无电学损耗，无仿真 Qp 先验；仅实测模型")
        flags.append("计算网格，制造版图/步长需设计与工艺确认")
        risks.append("；".join(flags))
        wins_p, dom_p = dominance(row.qp_prediction, row.k2eff_pct_prediction, frontier)
        wins_l, dom_l = dominance(row.qp_lower, row.k2eff_pct_lower, frontier)
        deltas.append(dict(
            qp_delta_vs_qp_baseline=row.qp_prediction - BASELINE_QP["qp"],
            qp_lower_vs_qp_baseline=row.qp_lower - BASELINE_QP["qp"],
            k2_delta_vs_k2_baseline=row.k2eff_pct_prediction - BASELINE_K2["k2eff_pct"],
            k2_lower_vs_k2_baseline=row.k2eff_pct_lower - BASELINE_K2["k2eff_pct"],
            dominates_frontier_point=";".join(wins_p),
            dominated_by_frontier_point=";".join(dom_p),
            dominates_frontier_lower=";".join(wins_l),
        ))
    frame["risk_flags"] = risks
    frame = pd.concat([frame, pd.DataFrame(deltas)], axis=1)
    frame["recommendation"] = [
        "不推荐（风险标注演示）"
        if g == "C"
        else ("待实测验证候选" if g == "A" else "插值候选（待确认版图）")
        for g in frame["group"]
    ]
    frame.to_csv(args.out / "candidates.csv", index=False)

    layers = []
    for caliber, qc, kc in [("point", "qp_prediction", "k2eff_pct_prediction"),
                            ("lower_bound", "qp_lower", "k2eff_pct_lower")]:
        work = frame[frame.group.isin(["A", "B"])].copy()
        work["rank_qp_first"] = work[qc].rank(ascending=False, method="first").astype(int)
        work["rank_k2_first"] = work[kc].rank(ascending=False, method="first").astype(int)
        work["pareto_balanced"] = pareto_layer(work, qc, kc)
        for _, r in work.iterrows():
            layers.append(dict(
                candidate_id=r.candidate_id, caliber=caliber,
                rank_qp_first=int(r.rank_qp_first), rank_k2_first=int(r.rank_k2_first),
                pareto_balanced=bool(r.pareto_balanced),
                qp_value=r[qc], k2_value=r[kc],
            ))
    pd.DataFrame(layers).to_csv(args.out / "rankings.csv", index=False)

    top = frame[frame.group.isin(["A", "B"])].sort_values(
        ["group", "qp_prediction"], ascending=[True, False]
    )
    lines = [
        "# P11 候选最小实测验证方案（冻结模板）",
        "",
        "测量前冻结：本文件、candidates.csv 与全部预测值/区间。"
        "任何阈值收紧须在测量前完成并另存版本。",
        "",
        "1. 版图与工艺确认：候选尺寸为计算网格；节距/形状沿用同系列设计规则，"
        "由设计方确认可制造性后才流片。",
        "2. 每候选结构同工艺流片；每结构 ≥12 个坐标位置；同批次测量；"
        "去嵌用 original/vz 配对（P6G 已验证可复现）。",
        "3. 同批次包含 C6-1 与 E1-1 同结构器件作基线复测对照。",
        "4. 判定：结构典型值落入 P3 预测区间，且 Qp 或 k² 至少一项超过对应基线"
        "（C6-1 Qp=1395.96 / E1-1 k²=14.6745%）记该候选通过；"
        "失败方向降级为开发数据，再推荐需新的独立测试点。",
        "5. 建议最小集合：A 组 4 个 + B 组中保守口径下界最接近前沿的 2 个；C 组演示点不进入制造。",
        "",
        "候选清单（预测值均为待验证假设，不是已证实更优）：",
        "",
        "| 候选 | 组 | EG | FL | 面积 | PF | Qp 点预测 [区间] | k² 点预测 [区间] | 推荐级别 |",
        "|---|---|---:|---:|---:|---:|---|---|---|",
    ]
    for r in top.itertuples():
        lines.append(
            f"| {r.candidate_id} | {r.group} | {r.eg_um} | {r.fl_um} | {int(r.area_um2)} | "
            f"{r.has_pf} | {r.qp_prediction:.1f} [{r.qp_lower:.1f}, {r.qp_upper:.1f}] | "
            f"{r.k2eff_pct_prediction:.3f} [{r.k2eff_pct_lower:.3f}, {r.k2eff_pct_upper:.3f}] | "
            f"{r.recommendation} |"
        )
    (args.out / "falsification_plan.md").write_text("\n".join(lines) + "\n")

    for key, root in [("p3", args.p3_root), ("p4", args.p4_root), ("p6c", args.p6c_root)]:
        files = manifests[key].get("files", manifests[key])
        for rel, info in files.items():
            digest = info["sha256"] if isinstance(info, dict) else info
            assert sha256(root / rel) == digest, rel
    assert all(sha256(Path(p)) == h for p, h in hashes.items())
    write_json(
        args.out / "summary.json",
        dict(
            status="P11_recommendation_candidates_not_verified",
            n_candidates=len(frame),
            n_excluded_directions=len(EXCLUSIONS),
            models_are_frozen_artifacts=True,
            selection_uses_outer_test="not_applicable_no_model_selection",
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
