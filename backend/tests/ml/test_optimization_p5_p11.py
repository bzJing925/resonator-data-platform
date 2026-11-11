"""P5 viewer P11 candidate block: extraction, exclusion, fail-closed validation."""

import pandas as pd
import pytest

from app.ml.optimization_data import sha256, write_json
from app.ml.optimization_p11_view import make_p11_candidates, verify_p11_products


def p11_tree(root):
    root.mkdir(parents=True)
    rows = []
    for i in range(10):
        group = "A" if i < 4 else "B"
        rows.append(
            dict(
                candidate_id=f"cand{i:02d}", group=group, eg_um=2.5 if group == "A" else 1.25,
                fl_um=0.25 * i, area_um2=5500.0, has_pf=0, branch_id="pf0_eg1_fl1",
                qp_prediction=1200.0 + i, qp_lower=1100.0 + i, qp_upper=1300.0 + i,
                k2eff_pct_prediction=14.5, k2eff_pct_lower=14.1, k2eff_pct_upper=14.9,
                p6c_qp=1190.0 + i, p6c_k2eff_pct=14.4, p3_support="outside_training_bounds",
                extrapolation_axis="eg_um", extrapolation_distance=0.5,
                qp_delta_vs_qp_baseline=-100.0, k2_delta_vs_k2_baseline=-0.1,
                risk_flags="演示", recommendation="待实测验证候选",
            )
        )
    rows.append(
        dict(rows[0], candidate_id="cand_c", group="C", area_um2=700.0, has_pf=1,
             branch_id="pf1_eg0_fl1", recommendation="不推荐（风险标注演示）")
    )
    frame = pd.DataFrame(rows)
    frame.to_csv(root / "candidates.csv", index=False)
    write_json(root / "verification.json", {"all_passed": True})
    write_json(
        root / "outputs_manifest.json",
        {
            str(p.relative_to(root)): sha256(p)
            for p in sorted(root.rglob("*"))
            if p.is_file() and p.name != "outputs_manifest.json"
        },
    )
    return root


def test_p11_candidates_extract_ab_only(tmp_path):
    block = make_p11_candidates(p11_tree(tmp_path / "p11"), {"existing_structure"})
    assert block["schema"] == "aln_optimization_p11_candidates_v1"
    assert len(block["entries"]) == 10
    assert all(e["group"] in ("A", "B") for e in block["entries"])
    assert "cand_c" not in {e["candidate_id"] for e in block["entries"]}  # C group stays out.
    entry = block["entries"][0]
    assert entry["qp"]["lower"] < entry["qp"]["prediction"] < entry["qp"]["upper"]
    assert block["experimental"] and not block["new_blind_test"]


def test_package_without_p11_root_is_byte_identical_to_d2_only_build():
    import json
    from pathlib import Path

    from scripts.build_optimization_p5 import make_package

    backend = Path(__file__).resolve().parents[2]
    package = make_package(
        backend / "surrogate_data/optimization_p4_20261016",
        backend / "surrogate_data/optimization_p10_20261026",
    )
    encoded = json.dumps(package, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    reference = (
        backend
        / "surrogate_data/optimization_p10_20261026/viewer_build/browser_data.json"
    ).read_text()
    assert encoded == reference  # No --p11-root: the package is exactly the D2-only build.


def test_package_without_p12_root_is_byte_identical_to_p11_build():
    import json
    from pathlib import Path

    from scripts.build_optimization_p5 import make_package

    backend = Path(__file__).resolve().parents[2]
    package = make_package(
        backend / "surrogate_data/optimization_p4_20261016",
        backend / "surrogate_data/optimization_p10_20261026",
        backend / "surrogate_data/optimization_p11_20261026",
    )
    encoded = json.dumps(package, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    reference = (
        backend
        / "surrogate_data/optimization_p11_20261026/viewer_build/browser_data.json"
    ).read_text()
    assert encoded == reference  # No --p12-root: the package is exactly the P11 build.


def test_p11_candidates_fail_closed(tmp_path):
    root = p11_tree(tmp_path / "p11")
    with pytest.raises(ValueError, match="collides"):
        make_p11_candidates(root, {"cand00"})
    frame = pd.read_csv(root / "candidates.csv")
    frame.loc[0, "qp_upper"] = frame.loc[0, "qp_lower"] - 1
    frame.to_csv(root / "candidates.csv", index=False)
    with pytest.raises(ValueError, match="checksum"):
        verify_p11_products(root)
    write_json(
        root / "outputs_manifest.json",
        {
            str(p.relative_to(root)): sha256(p)
            for p in sorted(root.rglob("*"))
            if p.is_file() and p.name != "outputs_manifest.json"
        },
    )
    with pytest.raises(ValueError, match="interval order"):
        make_p11_candidates(root, {"existing_structure"})
