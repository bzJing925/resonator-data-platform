"""P5 viewer D2 reference block: builder extraction and fail-closed validation."""


import pandas as pd
import pytest

from app.ml.optimization_d2_view import make_d2_reference, verify_p10_products
from app.ml.optimization_data import sha256, write_json

ARMS = ("D0_control", "D1_subset_hetero_interval", "D2_subset_c1_switch")
SUBSET_IDS = [f"s_{i:014x}" for i in range(8)]


def measured_catalog():
    rows = []
    for i, sid in enumerate(SUBSET_IDS):
        rows.append(
            dict(structure_id=sid, eg_um=0.0, fl_um=0.25 * i, area_um2=700.0, has_pf=1)
        )
    rows.append(dict(structure_id="s_other", eg_um=1.0, fl_um=1.0, area_um2=2500.0, has_pf=0))
    return pd.DataFrame(rows)


def p10_tree(root, n_subset=8, all_passed=True):
    root.mkdir(parents=True)
    rows = []
    for i in range(n_subset):
        for interval in ("joint_global", "joint_scaled"):
            for arm in ARMS:
                lo = 300.0 + 10 * i + (arm == "D2_subset_c1_switch") * 50
                rows.append(
                    dict(
                        structure_id=SUBSET_IDS[i],
                        arm=arm,
                        interval=interval,
                        fl_um=0.25 * i,
                        actual=500.0 + i,
                        lower=lo,
                        upper=lo + 100,
                    )
                )
    small = pd.DataFrame(rows)
    small.to_csv(root / "small_area_pf_qp.csv", index=False)
    (root / "experiment1_intervals").mkdir(exist_ok=True)
    small.to_csv(root / "experiment1_intervals/small_area_pf_qp.csv", index=False)
    write_json(root / "selection_e1.json", {"selected": "D0_control"})
    write_json(root / "verification.json", {"all_passed": all_passed})
    write_json(
        root / "outputs_manifest.json",
        {
            str(p.relative_to(root)): sha256(p)
            for p in sorted(root.rglob("*"))
            if p.is_file() and p.name != "outputs_manifest.json"
        },
    )
    return root


def test_d2_reference_extracts_eight_subset_entries(tmp_path):
    root = p10_tree(tmp_path / "p10")
    block = make_d2_reference(root, measured_catalog())
    assert block["schema"] == "aln_optimization_d2_reference_v1"
    assert block["experimental"] and not block["frozen_config"] and not block["new_blind_test"]
    assert len(block["entries"]) == 8
    entry = block["entries"][0]
    assert entry["d2_scaled"][0] > entry["c0_scaled"][0]  # Wider D2 interval starts higher here.
    assert entry["c0_scaled"][0] < entry["c0_scaled"][1]
    assert verify_p10_products(root)


def test_d2_reference_fails_closed_on_bad_evidence_or_geometry(tmp_path):
    root = p10_tree(tmp_path / "p10", all_passed=False)
    with pytest.raises(ValueError, match="verification"):
        make_d2_reference(root, measured_catalog())
    root = p10_tree(tmp_path / "p10b", n_subset=7)
    with pytest.raises(ValueError, match="eight"):
        make_d2_reference(root, measured_catalog())
    root = p10_tree(tmp_path / "p10c")
    catalog = measured_catalog()
    catalog.loc[0, "area_um2"] = 900.0
    with pytest.raises(ValueError, match="geometry"):
        make_d2_reference(root, catalog)


def test_d2_reference_rejects_tampered_products(tmp_path):
    root = p10_tree(tmp_path / "p10")
    small_path = root / "experiment1_intervals/small_area_pf_qp.csv"
    frame = pd.read_csv(small_path)
    frame.loc[0, "upper"] = frame.loc[0, "lower"]  # Break interval order.
    frame.to_csv(small_path, index=False)
    with pytest.raises(ValueError, match="checksum"):
        verify_p10_products(root)
