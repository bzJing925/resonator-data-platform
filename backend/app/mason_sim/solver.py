"""FBAR / EPPE Mason 1D 求解器。

出处：vendor 自 ADSML train-export 工作区 ``src/mason_solver.py``
（客户 CLI 脚本移植版，源自客户 ADS/Mason 手写 MNA 脚本）。
本次为 IUS2026 论文修订扩展：

1. ``piezo_intermediates`` / ``add_piezo_layer_four_port`` 新增 ``reverse``
   形参（默认 +1.0，保持旧行为）：极性反转 = Mason 理想变压器变比 N 取负，
   约定与客户原始脚本 ``mason_piezo_layer.py`` 的 ``piezo_formula_dict``
   完全一致：``N = sqrt(2*theta/(kt2*w*C0*Z)) * reverse``，reverse 只作用在
   变压器变比符号上。
2. 新增 EPPE（极性反转双层压电，无中间电极）一端口模型：
   ``build_eppe_one_port`` / ``solve_eppe_one_port`` / ``run_eppe_sweep``。

提供完整的 AlN/Mo FBAR 一端口 Mason 模型求解：
- 顶部 Mo 电极 + 压电层 + 底部 Mo 电极 + 空气背衬
- 频域 MNA（Modified Nodal Analysis）求解
- 输出 Z11、S11、|Z|(dBΩ)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.mason_sim.materials import AcousticMaterial, PiezoMaterial

# dB/m → Np/m 换算因子 20*log10(e)，用于把声学衰减 alpha (dB/m)
# 写入传播常数 k = 2*pi*f/v * (1 - j*alpha/DB_PER_NEPER)（沿用 vendor/ADS 写法）
DB_PER_NEPER = 8.686


# ============================================================
# Utilities
# ============================================================
def db20(x: np.ndarray) -> np.ndarray:
    """20 * log10(|x|), 保护小值。"""
    return 20.0 * np.log10(np.maximum(np.abs(x), 1e-300))


def build_frequency_grid(
    f_start_hz: float,
    f_stop_hz: float,
    f_step_hz: float | None = None,
    n_freqs: int | None = None,
) -> np.ndarray:
    """构建等距频率网格。

    优先使用 f_step_hz；若未提供则使用 n_freqs。
    """
    if f_step_hz is not None:
        n_freqs = int(np.round((f_stop_hz - f_start_hz) / f_step_hz)) + 1
        return np.linspace(f_start_hz, f_stop_hz, n_freqs)
    if n_freqs is not None:
        return np.linspace(f_start_hz, f_stop_hz, n_freqs)
    raise ValueError("Must provide either f_step_hz or n_freqs")


def s11_from_z11(z11: np.ndarray, zref: float = 50.0) -> np.ndarray:
    """由输入阻抗 Z11 计算 S11。"""
    return (z11 - zref) / (z11 + zref)


# ============================================================
# Parameter classes
# ============================================================
@dataclass(frozen=True)
class FBARParams:
    """FBAR（EPE 单层压电）几何参数。

    Attributes:
        t_aln: 压电层厚度 (m)
        t_mo: Mo 电极厚度 (m)，上下电极对称
        area: 有源区面积 (m^2)
        alpha: 压电层声学衰减系数 (dB/m, 默认 0.02)
        zref: 参考阻抗 (Ω)，默认 50
    """

    t_aln: float
    t_mo: float
    area: float
    alpha: float = 0.02
    zref: float = 50.0


@dataclass(frozen=True)
class EPEStackParams:
    """EPE 单层堆叠几何参数（非对称电极 + 可选 PF 质量加载层）。

    堆叠（自上而下）：
        PF 质量加载层(t_pf, t_pf=0 时跳过) / Top Mo(t_top) / Piezo(t_piezo) / Bottom Mo(t_bot)

    Attributes:
        t_piezo: 压电层厚度 (m)
        t_top: 顶 Mo 电极厚度 (m)
        t_bot: 底 Mo 电极厚度 (m)
        t_pf: PF 钝化层厚度 (m)，0 表示无 PF 层
        area: 有源区面积 (m^2)
        alpha: 压电层声学衰减系数 (dB/m, 默认 0.02)
        zref: 参考阻抗 (Ω)，默认 50
    """

    t_piezo: float
    t_top: float
    t_bot: float
    t_pf: float
    area: float
    alpha: float = 0.02
    zref: float = 50.0


@dataclass(frozen=True)
class EPPEParams:
    """EPPE（极性反转双层压电，无中间电极）几何参数。

    堆叠（自下而上）：
        底 Mo(t_mo_bot) / 下层压电(reverse_bot) / 上层压电(reverse_top) / 顶 Mo(t_mo_top)

    两层压电声学端口直连级联（下层顶面节点 = 上层底面节点），
    电学子网经悬浮内节点串联于顶底电极之间。

    Attributes:
        t_piezo_bot: 下层压电厚度 (m)
        t_piezo_top: 上层压电厚度 (m)
        t_mo_bot: 底 Mo 电极厚度 (m)
        t_mo_top: 顶 Mo 电极厚度 (m)
        area: 有源区面积 (m^2)
        alpha: 压电层声学衰减系数 (dB/m, 默认 0.02)
        reverse_bot: 下层压电极性符号（+1 正极性 / -1 反极性），默认 +1
        reverse_top: 上层压电极性符号，默认 -1（EPPE 反极性）
        zref: 参考阻抗 (Ω)，默认 50
    """

    t_piezo_bot: float
    t_piezo_top: float
    t_mo_bot: float
    t_mo_top: float
    area: float
    alpha: float = 0.02
    reverse_bot: float = +1.0
    reverse_top: float = -1.0
    zref: float = 50.0


# ============================================================
# MNA circuit
# ============================================================
GND_NAMES = {None, 0, "0", "gnd", "GND", "ground", "GROUND"}


def is_ground(node: str | None) -> bool:
    return node in GND_NAMES


class MNACircuit:
    """频域 MNA 电路对象。

    支持：
    - 导纳/阻抗 stamp
    - 理想电压源 stamp（含 0V 短接）
    - ADS/Mason 风格理想变压器 stamp
    """

    def __init__(self) -> None:
        self.nodes: set[str] = set()
        self.extras: list[tuple[str, str, int]] = []
        self.stamps: list[tuple] = []

    def _touch(self, node: str | None) -> None:
        if not is_ground(node):
            self.nodes.add(str(node))

    def add_admittance(self, n1: str | None, n2: str | None, y: complex) -> None:
        self._touch(n1)
        self._touch(n2)
        self.stamps.append(("Y", n1, n2, y))

    def add_impedance(self, n1: str | None, n2: str | None, z: complex) -> None:
        self.add_admittance(n1, n2, 1.0 / z)

    def add_vsource(
        self,
        n_plus: str | None,
        n_minus: str | None,
        value: complex,
        name: str,
    ) -> tuple[str, str, int]:
        self._touch(n_plus)
        self._touch(n_minus)
        label = ("extra", name, len(self.extras))
        self.extras.append(label)
        self.stamps.append(("V", n_plus, n_minus, value, label))
        return label

    def add_short(
        self,
        n1: str | None,
        n2: str | None,
        name: str,
    ) -> tuple[str, str, int]:
        """用 0V 理想电压源保留显式短接关系。"""
        return self.add_vsource(n1, n2, 0.0, name)

    def add_transformer_ads(
        self,
        p_plus: str | None,
        p_minus: str | None,
        s_plus: str | None,
        s_minus: str | None,
        turns: complex,
        name: str,
    ) -> tuple[tuple[str, str, int], tuple[str, str, int]]:
        """ADS/Mason 风格理想变压器。

        约束：
        - V_sec = N * V_pri
        - I_pri + N * I_sec = 0
        """
        self._touch(p_plus)
        self._touch(p_minus)
        self._touch(s_plus)
        self._touch(s_minus)

        ip = ("extra", f"{name}_ip", len(self.extras))
        self.extras.append(ip)
        isec = ("extra", f"{name}_is", len(self.extras))
        self.extras.append(isec)
        self.stamps.append(("XF", p_plus, p_minus, s_plus, s_minus, turns, ip, isec))
        return ip, isec

    def _idx(
        self,
        item: object,
        node_idx: dict[str, int],
        extra_idx: dict[tuple[str, str, int], int],
    ) -> int | None:
        if is_ground(item):
            return None
        if isinstance(item, tuple) and len(item) >= 1 and item[0] == "extra":
            return extra_idx[item]
        return node_idx[str(item)]

    def _stamp_vsource(
        self,
        a: np.ndarray,
        b: np.ndarray,
        node_idx: dict[str, int],
        extra_idx: dict[tuple[str, str, int], int],
        n_plus: str | None,
        n_minus: str | None,
        value: complex,
        label: tuple[str, str, int],
    ) -> None:
        p = self._idx(n_plus, node_idx, extra_idx)
        m = self._idx(n_minus, node_idx, extra_idx)
        k_idx = self._idx(label, node_idx, extra_idx)

        if p is not None:
            a[p, k_idx] += 1.0
            a[k_idx, p] += 1.0
        if m is not None:
            a[m, k_idx] -= 1.0
            a[k_idx, m] -= 1.0
        b[k_idx] += value

    def _stamp_transformer_ads(
        self,
        a: np.ndarray,
        node_idx: dict[str, int],
        extra_idx: dict[tuple[str, str, int], int],
        p_plus: str | None,
        p_minus: str | None,
        s_plus: str | None,
        s_minus: str | None,
        turns: complex,
        ip_label: tuple[str, str, int],
        is_label: tuple[str, str, int],
    ) -> None:
        pp = self._idx(p_plus, node_idx, extra_idx)
        pm = self._idx(p_minus, node_idx, extra_idx)
        sp = self._idx(s_plus, node_idx, extra_idx)
        sm = self._idx(s_minus, node_idx, extra_idx)
        ip = self._idx(ip_label, node_idx, extra_idx)
        isec = self._idx(is_label, node_idx, extra_idx)

        # KCL
        if pp is not None:
            a[pp, ip] += 1.0
        if pm is not None:
            a[pm, ip] -= 1.0
        if sp is not None:
            a[sp, isec] += 1.0
        if sm is not None:
            a[sm, isec] -= 1.0

        # V_sec = N * V_pri
        if pp is not None:
            a[ip, pp] += 1.0
        if pm is not None:
            a[ip, pm] -= 1.0
        if sp is not None:
            a[ip, sp] -= 1.0 / turns
        if sm is not None:
            a[ip, sm] += 1.0 / turns

        # I_pri + N * I_sec = 0
        a[isec, ip] += 1.0
        a[isec, isec] += turns

    def solve(self) -> tuple[np.ndarray, dict[str, int], dict[tuple[str, str, int], int]]:
        node_list = sorted(self.nodes)
        node_idx = {name: i for i, name in enumerate(node_list)}
        extra_idx = {lab: len(node_list) + k for k, lab in enumerate(self.extras)}

        n_unknown = len(node_list) + len(self.extras)
        a = np.zeros((n_unknown, n_unknown), dtype=complex)
        b = np.zeros(n_unknown, dtype=complex)

        for stamp in self.stamps:
            kind = stamp[0]

            if kind == "Y":
                _, n1, n2, y = stamp
                i = self._idx(n1, node_idx, extra_idx)
                j = self._idx(n2, node_idx, extra_idx)
                if i is not None:
                    a[i, i] += y
                if j is not None:
                    a[j, j] += y
                if i is not None and j is not None:
                    a[i, j] -= y
                    a[j, i] -= y

            elif kind == "V":
                _, n_plus, n_minus, value, label = stamp
                self._stamp_vsource(a, b, node_idx, extra_idx, n_plus, n_minus, value, label)

            elif kind == "XF":
                _, p_plus, p_minus, s_plus, s_minus, turns, ip_label, is_label = stamp
                self._stamp_transformer_ads(
                    a,
                    node_idx,
                    extra_idx,
                    p_plus,
                    p_minus,
                    s_plus,
                    s_minus,
                    turns,
                    ip_label,
                    is_label,
                )
            else:
                raise ValueError(f"Unknown stamp type: {kind}")

        x = np.linalg.solve(a, b)
        return x, node_idx, extra_idx


# ============================================================
# Mason building blocks
# ============================================================
def _validate_reverse(reverse: float) -> None:
    """reverse 只允许 ±1（极性符号）。"""
    if abs(reverse) != 1.0:
        raise ValueError(f"reverse 必须为 +1 或 -1（极性符号），收到 {reverse!r}")


def piezo_intermediates(
    mat: PiezoMaterial,
    area: float,
    thk: float,
    freq: float,
    alpha: float,
    reverse: float = +1.0,
) -> dict[str, complex]:
    """计算单层压电层 Mason 中间变量。

    reverse 为极性符号（只允许 +1 正极性 / -1 反极性，其他取值抛 ValueError），
    只作用在理想变压器变比 N 的正负号上：
    N = sqrt(2*theta/(kt2*w*C0*Z)) * reverse。默认 +1.0，保持 vendor 旧行为。
    """
    _validate_reverse(reverse)
    w = 2.0 * np.pi * freq
    v = (mat.c33 / mat.rho) ** 0.5
    z_ac = mat.rho * v  # 声阻抗 Z
    kt2 = mat.e33**2 / (mat.c33 * mat.eps)
    c0 = mat.eps * area / thk  # 静电容 C0
    k = 2.0 * np.pi * freq / v * (1.0 - 1j * alpha / DB_PER_NEPER)
    theta = k * thk / 2.0
    za = 1j * z_ac * np.tan(theta)
    zc = -1j * z_ac / np.sin(2.0 * theta)
    # N = sqrt(2*theta / (kt2 * w * C0 * Z)) * reverse
    n_turns = np.sqrt((2.0 * theta) / (kt2 * w * c0 * z_ac)) * reverse
    return {
        "w": w,
        "v": v,
        "Z": z_ac,
        "kt2": kt2,
        "C0": c0,
        "k": k,
        "theta": theta,
        "Za": za,
        "Zc": zc,
        "N": n_turns,
    }


def acoustic_tline_impedances(
    mat: AcousticMaterial,
    thk: float,
    freq: float,
) -> tuple[complex, complex]:
    """计算声学传输线的串联阻抗 Za 与并联阻抗 Zb（T 型网络）。

    对于厚度为 t 的声学传输线，T 型等效：
        Za = j * Z * tan(k * t / 2)
        Zb = -j * Z * csc(k * t)
    其中 k = 2*pi*f/v * (1 - j*alpha/DB_PER_NEPER)。
    """
    z_ac = mat.rho * mat.v
    k = 2.0 * np.pi * freq / mat.v * (1.0 - 1j * mat.alpha / DB_PER_NEPER)
    theta = k * thk / 2.0
    za = 1j * z_ac * np.tan(theta)
    zb = -1j * z_ac / np.sin(2.0 * theta)
    return za, zb


def add_acoustic_tline(
    circ: MNACircuit,
    prefix: str,
    port_top: str | None,
    port_bot: str | None,
    mat: AcousticMaterial,
    thk: float,
    freq: float,
) -> None:
    """将声学传输线作为 T 型网络 stamp 进 MNA。

    节点：port_top --Za-- mid --Za-- port_bot
                  |
                 Zb
                  |
                 GND
    """
    za, zb = acoustic_tline_impedances(mat, thk, freq)
    mid = f"{prefix}_mid"
    circ.add_impedance(port_top, mid, za)
    circ.add_impedance(mid, port_bot, za)
    circ.add_impedance(mid, "0", zb)


def add_piezo_layer_four_port(
    circ: MNACircuit,
    prefix: str,
    port1: str | None,
    port2: str | None,
    port3: str | None,
    port4: str | None,
    mat: PiezoMaterial,
    area: float,
    thk: float,
    alpha: float,
    freq: float,
    reverse: float = +1.0,
) -> dict[str, object]:
    """按 ADS/Mason 方式把单层压电层四端口展开进 MNA。

    端口定义：
    - port3 / port4：上侧声学端口（层顶面 / 层底面）
    - port1 / port2：下侧电学端口（左 / 右）

    内部结构：
        port3 --Za-- a_mid --Za-- port4
                          |
                         Zc
                          |
                        a_bot

        理想变压器：原边 a_bot->0，副边 port1->sec_r，变比 N*reverse

        port1 --Zc0-- n_e
        sec_r --Zc0-- n_e
        n_e -- port2

    reverse 为极性符号（只允许 +1 / -1，其他取值抛 ValueError），
    经 piezo_intermediates 作用于变压器变比 N 的符号。
    """
    _validate_reverse(reverse)
    iv = piezo_intermediates(mat, area, thk, freq, alpha, reverse=reverse)

    a_mid = f"{prefix}_a_mid"
    a_bot = f"{prefix}_a_bot"
    sec_r = f"{prefix}_sec_r"
    n_e = f"{prefix}_n_e"

    # 上侧声学 T 网络
    circ.add_impedance(port3, a_mid, iv["Za"])
    circ.add_impedance(a_mid, port4, iv["Za"])
    circ.add_impedance(a_mid, a_bot, iv["Zc"])

    # Mason 理想变压器（reverse 已在 N 的符号中体现）
    circ.add_transformer_ads(
        p_plus=a_bot,
        p_minus="0",
        s_plus=port1,
        s_minus=sec_r,
        turns=iv["N"],
        name=f"{prefix}_TF",
    )

    # 下侧电学网络：两个 C0 支路分开
    z_c0 = 1.0 / (1j * iv["w"] * iv["C0"])
    circ.add_impedance(port1, n_e, z_c0)
    circ.add_impedance(sec_r, n_e, z_c0)
    circ.add_short(n_e, port2, name=f"{prefix}_NE_TO_P2")

    return {
        "intermediates": iv,
        "nodes": {"a_mid": a_mid, "a_bot": a_bot, "sec_r": sec_r, "n_e": n_e},
    }


# ============================================================
# Full FBAR (EPE) one-port model
# ============================================================
def build_fbar_one_port(
    circ: MNACircuit,
    freq: float,
    piezo: PiezoMaterial,
    electrode: AcousticMaterial,
    params: FBARParams,
) -> dict[str, object]:
    """构建完整 FBAR（EPE 单层）一端口电路。

    堆叠结构（从上到下）：
        Top Mo electrode
        Piezo layer
        Bottom Mo electrode

    声学边界：最外声学表面（顶 Mo 顶面 / 底 Mo 底面）的声学端口接地，
    在力-电压类比下对应应力自由边界（空气背衬近似），激励基模 TE1 谐振。

    电学输入：IN 接 Bottom Mo 电极，Top Mo 电极接地。
    """
    t_aln = params.t_aln
    t_mo = params.t_mo
    area = params.area
    alpha = params.alpha

    # 声学节点
    top_mo_bot = "top_mo_bot"  # 接压电层上表面
    aln_bot = "aln_bot"  # 接底部 Mo 上表面

    top_electrode = "0"  # 上电极接地
    bot_electrode = "IN"  # 下电极接信号

    # 顶部 Mo 电极（声学传输线），上表面接地
    add_acoustic_tline(
        circ=circ,
        prefix="TOP_MO",
        port_top="0",
        port_bot=top_mo_bot,
        mat=electrode,
        thk=t_mo,
        freq=freq,
    )

    # 压电层
    add_piezo_layer_four_port(
        circ=circ,
        prefix="ALN",
        port1=top_electrode,  # 上电极（接地）
        port2=bot_electrode,  # 下电极（信号）
        port3=top_mo_bot,  # 层顶面接顶部 Mo
        port4=aln_bot,  # 层底面
        mat=piezo,
        area=area,
        thk=t_aln,
        alpha=alpha,
        freq=freq,
    )

    # 底部 Mo 电极（声学传输线），下表面接地
    add_acoustic_tline(
        circ=circ,
        prefix="BOT_MO",
        port_top=aln_bot,  # 接压电层
        port_bot="0",
        mat=electrode,
        thk=t_mo,
        freq=freq,
    )

    # 1V 输入源：IN 对地
    src_label = circ.add_vsource("IN", "0", 1.0, "VIN")

    return {"src_label": src_label}


def solve_fbar_one_port(
    freq: float,
    piezo: PiezoMaterial,
    electrode: AcousticMaterial,
    params: FBARParams,
) -> tuple[complex, dict[str, object]]:
    """求解单个频点的 FBAR 输入阻抗 Z11。"""
    circ = MNACircuit()
    build_info = build_fbar_one_port(circ, freq, piezo, electrode, params)
    src_label = build_info["src_label"]

    x, _node_idx, extra_idx = circ.solve()
    i_in = -x[extra_idx[src_label]]
    z11 = 1.0 / i_in
    return z11, build_info


@dataclass
class FBARSweepResult:
    """FBAR / EPE 堆叠扫频结果（run_fbar_sweep 与 run_epe_stack_sweep 共用）。"""

    freq: np.ndarray
    z11: np.ndarray
    s11: np.ndarray
    abs_z11_db: np.ndarray
    abs_s11_db: np.ndarray
    params: FBARParams | EPEStackParams


def run_fbar_sweep(
    freq: np.ndarray,
    piezo: PiezoMaterial,
    electrode: AcousticMaterial,
    params: FBARParams,
) -> FBARSweepResult:
    """执行 FBAR（EPE）一端口扫频。"""
    z11 = np.zeros_like(freq, dtype=complex)

    for idx, f in enumerate(freq):
        z11[idx], _ = solve_fbar_one_port(f, piezo, electrode, params)

    s11 = s11_from_z11(z11, params.zref)

    return FBARSweepResult(
        freq=freq,
        z11=z11,
        s11=s11,
        abs_z11_db=db20(z11),
        abs_s11_db=db20(s11),
        params=params,
    )


# ============================================================
# EPE stack (asymmetric electrodes + optional PF mass-loading) one-port model
# ============================================================
def build_epe_stack_one_port(
    circ: MNACircuit,
    freq: float,
    piezo: PiezoMaterial,
    electrode: AcousticMaterial,
    pf_material: AcousticMaterial,
    params: EPEStackParams,
) -> dict[str, object]:
    """构建 EPE 单层堆叠（非对称电极 + 可选 PF 质量加载层）一端口电路。

    堆叠结构（从上到下）：
        PF 质量加载层（t_pf=0 时跳过）
        Top Mo electrode (t_top)
        Piezo layer (t_piezo)
        Bottom Mo electrode (t_bot)

    边界条件与 build_fbar_one_port 完全一致：
    - 最外声学表面（PF 顶面，或无 PF 时顶 Mo 顶面 / 底 Mo 底面）的声学端口接地，
      力-电压类比下对应应力自由边界（空气背衬近似），激励基模 TE1 谐振。
    - 顶电极接地、底电极接 IN；1V 源激励 IN 对地，z11 = 1 / i_in。

    节点命名与 stamp 顺序刻意与 build_fbar_one_port 保持一致：
    t_pf=0 且 t_top=t_bot 时两者 MNA 矩阵相同，z11 逐位一致（回归黄金判据）。
    """
    # 声学节点（与 build_fbar_one_port 同名，保证退化情形逐位一致）
    top_mo_bot = "top_mo_bot"  # 接压电层上表面
    aln_bot = "aln_bot"  # 接底部 Mo 上表面

    top_electrode = "0"  # 上电极接地
    bot_electrode = "IN"  # 下电极接信号

    # PF 质量加载层（声学传输线，无压电耦合），上表面接地；t_pf=0 时跳过
    if params.t_pf > 0.0:
        pf_bot = "pf_bot"  # PF 底面 = 顶 Mo 顶面
        add_acoustic_tline(
            circ=circ,
            prefix="PF",
            port_top="0",
            port_bot=pf_bot,
            mat=pf_material,
            thk=params.t_pf,
            freq=freq,
        )
        top_mo_top: str | None = pf_bot
    else:
        top_mo_top = "0"

    # 顶部 Mo 电极（声学传输线）
    add_acoustic_tline(
        circ=circ,
        prefix="TOP_MO",
        port_top=top_mo_top,
        port_bot=top_mo_bot,
        mat=electrode,
        thk=params.t_top,
        freq=freq,
    )

    # 压电层
    add_piezo_layer_four_port(
        circ=circ,
        prefix="ALN",
        port1=top_electrode,  # 上电极（接地）
        port2=bot_electrode,  # 下电极（信号）
        port3=top_mo_bot,  # 层顶面接顶部 Mo
        port4=aln_bot,  # 层底面
        mat=piezo,
        area=params.area,
        thk=params.t_piezo,
        alpha=params.alpha,
        freq=freq,
    )

    # 底部 Mo 电极（声学传输线），下表面接地
    add_acoustic_tline(
        circ=circ,
        prefix="BOT_MO",
        port_top=aln_bot,  # 接压电层
        port_bot="0",
        mat=electrode,
        thk=params.t_bot,
        freq=freq,
    )

    # 1V 输入源：IN 对地
    src_label = circ.add_vsource("IN", "0", 1.0, "VIN")

    return {"src_label": src_label}


def solve_epe_stack_one_port(
    freq: float,
    piezo: PiezoMaterial,
    electrode: AcousticMaterial,
    pf_material: AcousticMaterial,
    params: EPEStackParams,
) -> tuple[complex, dict[str, object]]:
    """求解单个频点的 EPE 堆叠输入阻抗 Z11。"""
    circ = MNACircuit()
    build_info = build_epe_stack_one_port(circ, freq, piezo, electrode, pf_material, params)
    src_label = build_info["src_label"]

    x, _node_idx, extra_idx = circ.solve()
    i_in = -x[extra_idx[src_label]]
    z11 = 1.0 / i_in
    return z11, build_info


def run_epe_stack_sweep(
    freq: np.ndarray,
    piezo: PiezoMaterial,
    electrode: AcousticMaterial,
    pf_material: AcousticMaterial,
    params: EPEStackParams,
) -> FBARSweepResult:
    """执行 EPE 堆叠一端口扫频（复用 FBARSweepResult 容器）。"""
    z11 = np.zeros_like(freq, dtype=complex)

    for idx, f in enumerate(freq):
        z11[idx], _ = solve_epe_stack_one_port(f, piezo, electrode, pf_material, params)

    s11 = s11_from_z11(z11, params.zref)

    return FBARSweepResult(
        freq=freq,
        z11=z11,
        s11=s11,
        abs_z11_db=db20(z11),
        abs_s11_db=db20(s11),
        params=params,
    )


# ============================================================
# EPPE (polarity-inverted bilayer piezo) one-port model
# ============================================================
def build_eppe_one_port(
    circ: MNACircuit,
    freq: float,
    piezo_bot: PiezoMaterial,
    piezo_top: PiezoMaterial,
    electrode: AcousticMaterial,
    params: EPPEParams,
) -> dict[str, object]:
    """构建 EPPE（极性反转双层压电，无中间电极）一端口电路。

    堆叠（自下而上）：
        Bottom Mo(t_mo_bot)
        piezo_bot(reverse_bot)，如 AlN(Al 极性, reverse=+1)
        piezo_top(reverse_top)，如 AlScN(N 极性, reverse=-1)
        Top Mo(t_mo_top)

    接线约定：
    - 每层压电 port3=层顶面、port4=层底面；两层声学端口在界面节点直连级联
      （piezo_bot 顶面节点 = piezo_top 底面节点 = eppe_iface），无中间电极质量。
    - 电学子网经悬浮内节点串联：下层 port1=悬浮节点 X、port2=IN；
      上层 port1=GND（顶电极）、port2=X。顶电极接地、底电极接 IN，
      与 build_fbar_one_port 的边界条件完全一致。
    - 空气背衬边界与 EPE 一致：最外声学表面（顶 Mo 顶面 / 底 Mo 底面）接地。
    - 1V 源激励 IN 对地，z11 = 1 / i_in。
    """
    area = params.area
    alpha = params.alpha

    # 声学节点
    iface = "eppe_iface"  # 两压电层声学直连界面（无中间电极）
    pz_bot_bottom = "eppe_pz_bot_bottom"  # 下层压电底面，接底 Mo 上表面
    pz_top_top = "eppe_pz_top_top"  # 上层压电顶面，接顶 Mo 下表面

    float_x = "eppe_float_x"  # 电学串联悬浮内节点
    top_electrode = "0"  # 顶电极接地
    bot_electrode = "IN"  # 底电极接信号

    # 底部 Mo 电极（声学传输线），下表面接地
    add_acoustic_tline(
        circ=circ,
        prefix="EPPE_BOT_MO",
        port_top=pz_bot_bottom,
        port_bot="0",
        mat=electrode,
        thk=params.t_mo_bot,
        freq=freq,
    )

    # 下层压电（如 AlN，Al 极性，reverse_bot=+1）
    add_piezo_layer_four_port(
        circ=circ,
        prefix="EPPE_PZ_BOT",
        port1=float_x,  # 电学串联悬浮内节点
        port2=bot_electrode,  # 底电极（信号）
        port3=iface,  # 层顶面 = 声学界面
        port4=pz_bot_bottom,  # 层底面
        mat=piezo_bot,
        area=area,
        thk=params.t_piezo_bot,
        alpha=alpha,
        freq=freq,
        reverse=params.reverse_bot,
    )

    # 上层压电（如 AlScN，N 极性，reverse_top=-1）
    add_piezo_layer_four_port(
        circ=circ,
        prefix="EPPE_PZ_TOP",
        port1=top_electrode,  # 顶电极（接地）
        port2=float_x,  # 电学串联悬浮内节点
        port3=pz_top_top,  # 层顶面接顶 Mo
        port4=iface,  # 层底面 = 声学界面
        mat=piezo_top,
        area=area,
        thk=params.t_piezo_top,
        alpha=alpha,
        freq=freq,
        reverse=params.reverse_top,
    )

    # 顶部 Mo 电极（声学传输线），上表面接地
    add_acoustic_tline(
        circ=circ,
        prefix="EPPE_TOP_MO",
        port_top="0",
        port_bot=pz_top_top,
        mat=electrode,
        thk=params.t_mo_top,
        freq=freq,
    )

    # 1V 输入源：IN 对地
    src_label = circ.add_vsource("IN", "0", 1.0, "VIN")

    return {"src_label": src_label, "float_node": float_x, "iface_node": iface}


def solve_eppe_one_port(
    freq: float,
    piezo_bot: PiezoMaterial,
    piezo_top: PiezoMaterial,
    electrode: AcousticMaterial,
    params: EPPEParams,
) -> tuple[complex, dict[str, object]]:
    """求解单个频点的 EPPE 输入阻抗 Z11。"""
    circ = MNACircuit()
    build_info = build_eppe_one_port(circ, freq, piezo_bot, piezo_top, electrode, params)
    src_label = build_info["src_label"]

    x, _node_idx, extra_idx = circ.solve()
    i_in = -x[extra_idx[src_label]]
    z11 = 1.0 / i_in
    return z11, build_info


@dataclass
class EPPESweepResult:
    """EPPE 扫频结果。"""

    freq: np.ndarray
    z11: np.ndarray
    s11: np.ndarray
    abs_z11_db: np.ndarray
    abs_s11_db: np.ndarray
    params: EPPEParams


def run_eppe_sweep(
    freq: np.ndarray,
    piezo_bot: PiezoMaterial,
    piezo_top: PiezoMaterial,
    electrode: AcousticMaterial,
    params: EPPEParams,
) -> EPPESweepResult:
    """执行 EPPE 一端口扫频。"""
    z11 = np.zeros_like(freq, dtype=complex)

    for idx, f in enumerate(freq):
        z11[idx], _ = solve_eppe_one_port(f, piezo_bot, piezo_top, electrode, params)

    s11 = s11_from_z11(z11, params.zref)

    return EPPESweepResult(
        freq=freq,
        z11=z11,
        s11=s11,
        abs_z11_db=db20(z11),
        abs_s11_db=db20(s11),
        params=params,
    )


# ============================================================
# Convenience helpers
# ============================================================
def nm_to_m(value_nm: float) -> float:
    return value_nm * 1e-9


def um2_to_m2(value_um2: float) -> float:
    return value_um2 * 1e-12
