"""Mason 求解器材料定义。

出处：vendor 自 ADSML train-export 工作区 ``src/materials.py``
（客户 CLI 脚本移植版）。本次为 IUS2026 修订新增真 AlN 材料 ALN1
（参数来自客户原始脚本 ``mason_piezo_layer.py`` 中的 ALN1 定义）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PiezoMaterial:
    """压电材料参数。

    Attributes:
        name: 材料名称
        rho: 密度 (kg/m^3)
        c33: 弹性常数 c33 (Pa)
        e33: 压电常数 e33 (C/m^2)
        eps: 介电常数 epsilon_33 (F/m)
    """

    name: str
    rho: float
    c33: float
    e33: float
    eps: float

    @property
    def velocity(self) -> float:
        """纵向声波速度 v = sqrt(c33 / rho)."""
        return (self.c33 / self.rho) ** 0.5

    @property
    def acoustic_impedance(self) -> float:
        """声阻抗 Z = rho * v."""
        return self.rho * self.velocity

    @property
    def kt2(self) -> float:
        """机电耦合系数 kt^2 = e33^2 / (c33 * eps)."""
        return self.e33**2 / (self.c33 * self.eps)


@dataclass(frozen=True)
class AcousticMaterial:
    """普通声学层材料参数（电极、衬底等）。

    Attributes:
        name: 材料名称
        rho: 密度 (kg/m^3)
        v: 纵向声波速度 (m/s)
        alpha: 衰减系数 (dB/m, 可选)
    """

    name: str
    rho: float
    v: float
    alpha: float = 0.0

    @property
    def acoustic_impedance(self) -> float:
        """声阻抗 Z = rho * v."""
        return self.rho * self.v


# ============================================================
# Canonical materials: AlScN / Mo / Air
# ============================================================

# AlScN 压电层材料参数（项目常用值）
ALSCN = PiezoMaterial(
    name="AlScN",
    rho=3280.17,
    c33=292466000000,
    e33=2.55313,
    eps=1.14621e-10,
)

# 真 AlN 压电层材料参数（来自客户脚本 ALN1）
ALN1 = PiezoMaterial(
    name="AlN",
    rho=3300.0,
    c33=419.0e9,
    e33=1.55,
    eps=7.96877e-11,
)

# Mo 电极材料参数
MO = AcousticMaterial(
    name="Mo",
    rho=10220.0,
    v=6290.0,
    alpha=0.0,
)

# 空气背衬
AIR = AcousticMaterial(
    name="Air",
    rho=1.225,
    v=343.0,
    alpha=0.0,
)

# 保留 ALN 别名以兼容旧代码；本项目 EPE 默认采用 AlScN 材料参数
ALN = ALSCN
