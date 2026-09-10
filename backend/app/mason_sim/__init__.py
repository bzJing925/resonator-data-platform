"""Mason 1D 物理求解器（EPE / EPPE）。

出处：vendor 自 ADSML train-export 工作区（客户 CLI 脚本移植版），
本次为 IUS2026 修订扩展 reverse 极性形参与 EPPE 极性反转双层结构。
"""

from app.mason_sim.materials import (
    AIR,
    ALN,
    ALN1,
    ALSCN,
    MO,
    AcousticMaterial,
    PiezoMaterial,
)
from app.mason_sim.solver import (
    EPPEParams,
    EPPESweepResult,
    FBARParams,
    FBARSweepResult,
    build_eppe_one_port,
    build_fbar_one_port,
    run_eppe_sweep,
    run_fbar_sweep,
    solve_eppe_one_port,
    solve_fbar_one_port,
)

__all__ = [
    "AIR",
    "ALN",
    "ALN1",
    "ALSCN",
    "MO",
    "AcousticMaterial",
    "EPPEParams",
    "EPPESweepResult",
    "FBARParams",
    "FBARSweepResult",
    "PiezoMaterial",
    "build_eppe_one_port",
    "build_fbar_one_port",
    "run_eppe_sweep",
    "run_fbar_sweep",
    "solve_eppe_one_port",
    "solve_fbar_one_port",
]
