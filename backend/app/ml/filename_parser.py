"""从 S1P 文件名提取器件参数。

文件名格式示例：
    S22_3_A1-1_X0Y0N20_Fail_de.s1p
    S22_3_A1-1_X0Y0N20_Pass.s1p

提取字段：
    - area_n:   A1 → 11 (A=1, B=2...)
    - x, y:     X0Y0 → x=0, y=0
    - pf:       Fail → "N", Pass → "Y"
    - deembedded: "de" → True, 无 → False
"""

from __future__ import annotations

import re

# area 字母到数字的映射
_AREA_LETTER_MAP = {c: i + 1 for i, c in enumerate("ABCDEFGHIJ")}

# area 字母到面积(μm²)的经验映射（示例值，可根据实际工艺调整）
_AREA_UM2_MAP = {
    "A": 100,
    "B": 200,
    "C": 300,
    "D": 400,
    "E": 500,
}

# 文件名正则
# S22_3_A1-1_X0Y0N20_Fail_de.s1p
# 或 S22_3_A1-1_X0Y0N20_Pass.s1p
_FILENAME_RE = re.compile(
    r"^S22_3_([A-Z])(\d+)-(\d+)_X(\d+)Y(\d+)N\d+_(\w+)(?:_(de))?\.s1p$"
)


def parse_filename_params(filename: str) -> dict[str, object]:
    """从文件名解析器件参数。

    Args:
        filename: s1p 文件名（不含路径）。

    Returns:
        dict 包含 area_n, area_um2, x, y, pf, deembedded。
        若解析失败，返回全默认值。
    """
    m = _FILENAME_RE.match(filename)
    if not m:
        return _default_params()

    area_letter = m.group(1)
    area_num = int(m.group(2))
    # device_idx = int(m.group(3))  # 未使用
    x = int(m.group(4))
    y = int(m.group(5))
    pf_raw = m.group(6)
    de_raw = m.group(7)

    area_n = _AREA_LETTER_MAP.get(area_letter, 0) * 10 + area_num
    area_um2 = _AREA_UM2_MAP.get(area_letter, 0)
    pf = "Y" if pf_raw.lower() == "pass" else "N"
    deembedded = de_raw == "de"

    return {
        "area_n": area_n,
        "area_um2": area_um2,
        "x": x,
        "y": y,
        "pf": pf,
        "deembedded": deembedded,
        # eg, fl, ag 无信息，默认 0.0
        "eg": 0.0,
        "fl": 0.0,
        "ag": 0.0,
    }


def _default_params() -> dict[str, object]:
    return {
        "area_n": 0,
        "area_um2": 0,
        "x": 0,
        "y": 0,
        "pf": "N",
        "deembedded": False,
        "eg": 0.0,
        "fl": 0.0,
        "ag": 0.0,
    }


def batch_parse_filenames(filenames: list[str]) -> list[dict[str, object]]:
    """批量解析文件名。"""
    return [parse_filename_params(f) for f in filenames]
