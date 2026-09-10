"""WP1 Mason EPPE 物理 sanity demo（给用户肉眼确认用）。

三个点：
  (a) EPPE：AlN(100nm, Al 极性, reverse=+1) / AlScN(200nm, N 极性, reverse=-1)，
      顶/底 Mo 各 150nm，area=1000um^2，alpha=0.02，8-22 GHz |Z| 谱，标 fs2/fp2。
      说明：EPPE 只以 >10 GHz 的 TE2 为有效模态（TE1 被极性反转对称性抑制）。
  (b) 对照：同总厚（AlScN 100+200nm）同材料同极性（reverse=+1,+1）。
      为在同一张图上同时看到 TE1（~5-6 GHz，低于 EPPE 分析窗）与
      "TE2 死"（10-16 GHz 无谐振），本图横轴放宽到 3-22 GHz，
      并用阴影标出与 (a) 相同的 8-22 GHz 分析窗。
  (c) EPE 参考：单层 AlScN + 同电极（Mo 150nm），3-8 GHz。
      注意偏离规格说明：规格写 t_piezo≈800nm 且 TE1 在 4-7 GHz，
      但 vendor 求解器（golden 锚定）下 800nm AlScN + 150nm Mo 的
      fs1=3.23 GHz（Mo 面密度超过压电层本身，负载把 TE1 拉低），
      为满足 TE1 落在 4-7 GHz 的判据，取 t_piezo=500nm（fs1=4.16 GHz）。

输出：backend/mason_sim_demo/*.png（PNG 不提交 git，目录内自带 .gitignore）。
终端打印每点的 fs1/fp1/kt2_te1/fs2/fp2/kt2_te2/Qs2/Qp2 数值表。

运行（backend/ 目录下）：
    uv run --no-sync python scripts/mason_eppe_sanity_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np

# 把 backend 目录加入路径，以便 import app（同 train_surrogate_stage1.py）
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.mason_sim.labels import extract_eppe_labels  # noqa: E402
from app.mason_sim.materials import ALN1, ALSCN, MO  # noqa: E402
from app.mason_sim.solver import (  # noqa: E402
    EPPEParams,
    FBARParams,
    build_frequency_grid,
    nm_to_m,
    run_eppe_sweep,
    run_fbar_sweep,
    um2_to_m2,
)

OUT_DIR = PROJECT_ROOT / "mason_sim_demo"

T_MO_NM = 150.0
AREA_UM2 = 1000.0
ALPHA = 0.02
TE2_WIN = (10e9, 16e9)  # TE2 分析窗（排除 ~17GHz 起的 TE3 奇模）
EPPE_VIEW = (8e9, 22e9)  # (a) EPPE 谱窗口


def _mark(ax, f_hz: float, color: str, label: str) -> None:
    if np.isfinite(f_hz):
        ax.axvline(f_hz / 1e9, color=color, ls="--", lw=1.2, alpha=0.85, label=label)


def _plot_a():
    p = EPPEParams(
        t_piezo_bot=nm_to_m(100.0),
        t_piezo_top=nm_to_m(200.0),
        t_mo_bot=nm_to_m(T_MO_NM),
        t_mo_top=nm_to_m(T_MO_NM),
        area=um2_to_m2(AREA_UM2),
        alpha=ALPHA,
        reverse_bot=+1.0,
        reverse_top=-1.0,
    )
    freq = build_frequency_grid(*EPPE_VIEW, f_step_hz=5e6)
    r = run_eppe_sweep(freq, ALN1, ALSCN, MO, p)
    lab_a = extract_eppe_labels(freq, r.z11, te2_f_min_hz=TE2_WIN[0], te2_f_max_hz=TE2_WIN[1])

    fig, ax = plt.subplots(figsize=(9, 5), constrained_layout=True)
    ax.plot(freq / 1e9, r.abs_z11_db, color="C0", lw=1.4, label="|Z11| EPPE (reverse +1/-1)")
    _mark(ax, lab_a.fs2_hz, "C3", f"fs2 = {lab_a.fs2_hz / 1e9:.3f} GHz")
    _mark(ax, lab_a.fp2_hz, "C2", f"fp2 = {lab_a.fp2_hz / 1e9:.3f} GHz")
    ax.set_title("(a) EPPE AlN(100nm,+1)/AlScN(200nm,-1), Mo 150/150nm — TE2 mode")
    ax.set_xlabel("Frequency (GHz)")
    ax.set_ylabel("|Z11| (dBΩ)")
    ax.grid(True, alpha=0.35)
    ax.legend()
    fig.savefig(OUT_DIR / "a_eppe_te2.png", dpi=150)
    plt.close(fig)
    return lab_a


def _plot_b():
    p = EPPEParams(
        t_piezo_bot=nm_to_m(100.0),
        t_piezo_top=nm_to_m(200.0),
        t_mo_bot=nm_to_m(T_MO_NM),
        t_mo_top=nm_to_m(T_MO_NM),
        area=um2_to_m2(AREA_UM2),
        alpha=ALPHA,
        reverse_bot=+1.0,
        reverse_top=+1.0,  # 同极性对照
    )
    freq = build_frequency_grid(3e9, 22e9, f_step_hz=5e6)
    r = run_eppe_sweep(freq, ALSCN, ALSCN, MO, p)
    lab_b = extract_eppe_labels(freq, r.z11, te2_f_min_hz=TE2_WIN[0], te2_f_max_hz=TE2_WIN[1])

    fig, ax = plt.subplots(figsize=(9, 5), constrained_layout=True)
    ax.axvspan(
        EPPE_VIEW[0] / 1e9,
        EPPE_VIEW[1] / 1e9,
        color="0.9",
        alpha=0.6,
        label="EPPE analysis window (8-22 GHz)",
    )
    ax.plot(freq / 1e9, r.abs_z11_db, color="C1", lw=1.4, label="|Z11| same-polarity (+1/+1)")
    _mark(ax, lab_b.fs1_hz, "C3", f"fs1 = {lab_b.fs1_hz / 1e9:.3f} GHz")
    _mark(ax, lab_b.fp1_hz, "C2", f"fp1 = {lab_b.fp1_hz / 1e9:.3f} GHz")
    ax.annotate(
        "TE2 dead: no resonance in 10-16 GHz\n(cf. EPPE TE2 at 14.1 GHz)",
        xy=(13.0, float(np.median(r.abs_z11_db))),
        xytext=(10.5, float(np.max(r.abs_z11_db)) * 0.98),
        fontsize=9,
        arrowprops={"arrowstyle": "->", "color": "0.4"},
    )
    ax.set_title("(b) Control: AlScN(100nm)/AlScN(200nm) same polarity — TE1 alive, TE2 dead")
    ax.set_xlabel("Frequency (GHz)")
    ax.set_ylabel("|Z11| (dBΩ)")
    ax.grid(True, alpha=0.35)
    ax.legend(loc="lower left")
    fig.savefig(OUT_DIR / "b_bilayer_same_polarity.png", dpi=150)
    plt.close(fig)
    return lab_b


def _plot_c():
    # 注意：规格 t_piezo≈800nm 在 vendor 求解器下 fs1=3.23 GHz（Mo 负载拉低），
    # 取 500nm 使 TE1 落在 4-7 GHz 判据内。
    t_piezo_nm = 500.0
    p = FBARParams(
        t_aln=nm_to_m(t_piezo_nm),
        t_mo=nm_to_m(T_MO_NM),
        area=um2_to_m2(AREA_UM2),
        alpha=ALPHA,
    )
    freq = build_frequency_grid(3e9, 8e9, f_step_hz=5e6)
    r = run_fbar_sweep(freq, ALSCN, MO, p)
    lab_c = extract_eppe_labels(freq, r.z11, te2_f_min_hz=TE2_WIN[0], te2_f_max_hz=TE2_WIN[1])

    fig, ax = plt.subplots(figsize=(9, 5), constrained_layout=True)
    ax.plot(freq / 1e9, r.abs_z11_db, color="C0", lw=1.4, label="|Z11| EPE single layer")
    _mark(ax, lab_c.fs1_hz, "C3", f"fs1 = {lab_c.fs1_hz / 1e9:.3f} GHz")
    _mark(ax, lab_c.fp1_hz, "C2", f"fp1 = {lab_c.fp1_hz / 1e9:.3f} GHz")
    ax.set_title(f"(c) EPE reference: AlScN {t_piezo_nm:.0f}nm, Mo 150/150nm — TE1 mode")
    ax.set_xlabel("Frequency (GHz)")
    ax.set_ylabel("|Z11| (dBΩ)")
    ax.grid(True, alpha=0.35)
    ax.legend()
    fig.savefig(OUT_DIR / "c_epe_te1_reference.png", dpi=150)
    plt.close(fig)
    return lab_c


def _fmt_ghz(f_hz: float) -> str:
    return f"{f_hz / 1e9:8.4f}" if np.isfinite(f_hz) else "     nan"


def _fmt_pct(x: float) -> str:
    return f"{x * 100.0:7.3f}" if np.isfinite(x) else "    nan"


def _fmt_q(x: float) -> str:
    return f"{x:8.1f}" if np.isfinite(x) else "     nan"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / ".gitignore").write_text("*.png\n", encoding="utf-8")

    lab_a = _plot_a()
    lab_b = _plot_b()
    lab_c = _plot_c()

    rows = [
        ("(a) EPPE AlN100/AlScN200 rev(+1/-1)", lab_a),
        ("(b) AlScN100/AlScN200 same(+1/+1)   ", lab_b),
        ("(c) EPE AlScN 500nm reference       ", lab_c),
    ]
    print()
    print("=== Mason EPPE sanity demo (2026-09-10, WP1) ===")
    print(f"TE2 search window: {TE2_WIN[0] / 1e9:.0f}-{TE2_WIN[1] / 1e9:.0f} GHz")
    print()
    header = (
        f"{'config':38s} {'fs1[GHz]':>9s} {'fp1[GHz]':>9s} {'kt2_te1[%]':>10s} "
        f"{'fs2[GHz]':>9s} {'fp2[GHz]':>9s} {'kt2_te2[%]':>10s} {'Qs2':>8s} {'Qp2':>8s}"
    )
    print(header)
    print("-" * len(header))
    for name, lab in rows:
        print(
            f"{name:38s} {_fmt_ghz(lab.fs1_hz):>9s} {_fmt_ghz(lab.fp1_hz):>9s} "
            f"{_fmt_pct(lab.kt2_te1):>10s} {_fmt_ghz(lab.fs2_hz):>9s} {_fmt_ghz(lab.fp2_hz):>9s} "
            f"{_fmt_pct(lab.kt2_te2):>10s} {_fmt_q(lab.qs2):>8s} {_fmt_q(lab.qp2):>8s}"
        )
    print()
    print("note(c): t_piezo=500nm (not ~800nm) so that TE1 falls in 4-7 GHz;")
    print("         at 800nm the vendor solver gives fs1=3.23 GHz (Mo loading).")
    print(f"PNGs written to: {OUT_DIR}")


if __name__ == "__main__":
    main()
