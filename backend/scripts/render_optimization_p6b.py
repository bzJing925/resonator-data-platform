"""Readable native-spectrum evidence for the frozen P6B diagnostic cases."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import skrf as rf


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--diagnostics", type=Path, required=True)
    ap.add_argument("--simulation-table", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    args.out.mkdir(parents=True)
    series = pd.read_csv(args.diagnostics / "eg_series_diagnostics.csv")
    cases = series[
        series.metric.eq("fp_ghz")
        & (
            series.original_direction.eq("nonmonotonic")
            | series.nearest_peak_continuity_mismatches.notna()
        )
    ]
    sim = pd.read_csv(args.simulation_table).set_index("Training_Row_ID")
    probes = pd.read_csv(args.diagnostics / "simulation_peak_probes.csv").set_index(
        "Training_Row_ID"
    )
    colors = ["#0072B2", "#E69F00", "#009E73", "#CC79A7"]
    plt.rcParams.update({"font.size": 10})
    fig, axes = plt.subplots(len(cases), 2, figsize=(11, 3.5 * len(cases)), squeeze=False)
    for i, case in enumerate(cases.itertuples()):
        group = sim.loc[case.row_ids.split(";")].sort_values("Train_L_EG_um")
        zoom_low, zoom_high = group.fp_ghz.min() - 0.003, group.fp_ghz.max() + 0.003
        zoom_values = []
        for color, (sid, row) in zip(colors, group.iterrows(), strict=True):
            net = rf.Network(row.resolved_path)
            freq = net.f / 1e9
            db = 20 * np.log10(np.abs(net.z[:, 0, 0]))
            for ax in axes[i]:
                ax.plot(freq, db, lw=1.3, color=color, label=f"EG {row.Train_L_EG_um:g} um")
            mask = (freq >= zoom_low) & (freq <= zoom_high)
            zoom_values.extend(db[mask])
            ix = np.argmin(np.abs(net.f - probes.loc[sid, "fp_hz"]))
            axes[i, 1].plot(freq[ix], db[ix], "o", color=color, ms=5)
        axes[i, 0].set_xlim(group.fs_ghz.min() - 0.015, group.fp_ghz.max() + 0.015)
        axes[i, 1].set_xlim(zoom_low, zoom_high)
        pad = max(0.08, (max(zoom_values) - min(zoom_values)) * 0.1)
        axes[i, 1].set_ylim(min(zoom_values) - pad, max(zoom_values) + pad)
        for ax in axes[i]:
            ax.set(xlabel="Frequency (GHz)", ylabel="20 log10 |Z / ohm|")
            ax.legend(fontsize=8)
        axes[i, 0].set_title(
            f"Area {group.Train_A_res_um2.iloc[0]:g} um2, FL {group.Train_L_FL_um.iloc[0]:g} um"
        )
        axes[i, 1].set_title("Dots: selected fp; local spacing 0.2 MHz")
    fig.tight_layout()
    fig.savefig(args.out / "peak_evidence.png", dpi=180)
    fig.savefig(args.out / "peak_evidence.pdf")
    plt.close(fig)


if __name__ == "__main__":
    main()
