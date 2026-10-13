"""Canonical numeric units shared by query metadata and analysis validation."""

NUMERIC_UNITS = {
    "fs_ghz": "GHz",
    "fp_ghz": "GHz",
    "fs2_ghz": "GHz",
    "fp2_ghz": "GHz",
    "fbode_ghz": "GHz",
    "zs_ohm": "Ω",
    "zp_ohm": "Ω",
    "zs2_ohm": "Ω",
    "zp2_ohm": "Ω",
    "qs": "1",
    "qp": "1",
    "qs_bodeq": "1",
    "qp_bodeq": "1",
    "bodeq_fitted": "1",
    "bodeq_smooth": "1",
    "bodeq_raw": "1",
    "dbqs": "dB",
    "dbqp": "dB",
    "k2eff_pct": "%",
    "area_um2": "µm²",
}
COMPARISON_METRICS = {"fs_ghz", "fp_ghz", "qs", "qp", "k2eff_pct", "zs_ohm", "zp_ohm"}
