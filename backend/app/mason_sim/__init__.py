"""Mason 1D 物理求解器（EPE / EPPE）。

出处：vendor 自 ADSML train-export 工作区（客户 CLI 脚本移植版），
本次为 IUS2026 修订扩展 reverse 极性形参与 EPPE 极性反转双层结构。
"""

from app.mason_sim.labels import (
    EPPELabels,
    compute_q_bode_phase,
    estimate_q_factor,
    extract_eppe_labels,
    find_te1_resonances,
    find_te2_resonances,
    kt2_identity,
)
from app.mason_sim.materials import ALN1, ALSCN, MO, AcousticMaterial, PiezoMaterial
from app.mason_sim.solver import (
    EPPEParams,
    EPPESweepResult,
    FBARParams,
    FBARSweepResult,
    MNACircuit,
    acoustic_tline_impedances,
    add_acoustic_tline,
    add_piezo_layer_four_port,
    build_eppe_one_port,
    build_fbar_one_port,
    build_frequency_grid,
    db20,
    nm_to_m,
    piezo_intermediates,
    run_eppe_sweep,
    run_fbar_sweep,
    s11_from_z11,
    solve_eppe_one_port,
    solve_fbar_one_port,
    um2_to_m2,
)

__all__ = [
    "ALN1",
    "ALSCN",
    "MO",
    "AcousticMaterial",
    "EPPELabels",
    "EPPEParams",
    "EPPESweepResult",
    "FBARParams",
    "FBARSweepResult",
    "MNACircuit",
    "PiezoMaterial",
    "acoustic_tline_impedances",
    "add_acoustic_tline",
    "add_piezo_layer_four_port",
    "build_eppe_one_port",
    "build_fbar_one_port",
    "build_frequency_grid",
    "compute_q_bode_phase",
    "db20",
    "estimate_q_factor",
    "extract_eppe_labels",
    "find_te1_resonances",
    "find_te2_resonances",
    "kt2_identity",
    "nm_to_m",
    "piezo_intermediates",
    "run_eppe_sweep",
    "run_fbar_sweep",
    "s11_from_z11",
    "solve_eppe_one_port",
    "solve_fbar_one_port",
    "um2_to_m2",
]
