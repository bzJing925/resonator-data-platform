"""文件名解析边界。"""

from __future__ import annotations

from app.core.filename import (
    extract_coord,
    extract_mark,
    extract_port,
    extract_xy,
    parse_filename,
)


def test_parse_dut_s2p() -> None:
    p = parse_filename("17_E6-1_X0Y0N18_Fail.s2p")
    assert p.mark == "E6-1"
    assert p.coord == "X0Y0"
    assert p.x == 0
    assert p.y == 0
    assert p.port is None  # 没拆分前 .s2p 不带 _S11/_S22
    assert p.pf == "Y"
    assert not p.is_open
    assert not p.is_short


def test_parse_split_s1p_s11() -> None:
    p = parse_filename("17_E6-1_X0Y0N18_Fail_S11.s1p")
    assert p.port == "S11"
    assert p.mark == "E6-1"


def test_parse_split_s1p_s22() -> None:
    p = parse_filename("17_E6-1_X0Y0N18_Fail_S22.s1p")
    assert p.port == "S22"


def test_parse_legacy_format_s1p() -> None:
    p = parse_filename("S11_2_E6-1_X0Y0N18.s1p")
    assert p.port == "S11"
    assert p.mark == "E6-1"


def test_calibration_open_short_basic() -> None:
    p_open = parse_filename("OPEN.s2p")
    p_short = parse_filename("SHORT.s2p")
    assert p_open.is_open and not p_open.is_short
    assert p_short.is_short and not p_short.is_open
    assert p_open.is_calibration and p_short.is_calibration


def test_calibration_open_short_with_underscore_suffix() -> None:
    """客户的真实校准文件名包含 _<数字> 或 -<数字> 后缀（de.py 第 86 行有据可查）。
    历史 bug：\\bOPEN\\b 因 _ 是 word-char → 不 match，校准文件被当成 DUT 静默
    走错路径。"""
    for name in ("OPEN_1.s2p", "OPEN-3.s2p", "cal_OPEN_2.s2p", "OPEN_X0Y0.s1p"):
        p = parse_filename(name)
        assert p.is_open, f"{name} 应被识别为 open，实际 is_open={p.is_open}"

    for name in ("SHORT_1.s2p", "SHORT-3.s2p", "cal_SHORT.s2p"):
        p = parse_filename(name)
        assert p.is_short, f"{name} 应被识别为 short，实际 is_short={p.is_short}"


def test_open_substring_does_not_false_positive_for_other_words() -> None:
    """OPENING / OPENED 等真包含 'OPEN' 的英文词不应被误识别为 open 文件。"""
    for name in ("OPENING_test.s2p", "OPENED_X0Y0.s2p"):
        p = parse_filename(name)
        assert not p.is_open, f"{name} 不应被识别为 open"


def test_extract_coord_normalizes_leading_zero() -> None:
    assert extract_coord("X007Y012N5.s2p") == "X7Y12"
    # 全 0 不应该返回空串
    assert extract_coord("X000Y000.s2p") == "X0Y0"
    # 负数: 实现只剥一层 0，对照客户真实文件名（不带 padding）来看够用；
    # 这里固定下当前行为防止意外回归。
    assert extract_coord("X-007Y-012N5.s2p") == "X-07Y-12"
    assert extract_coord("X-7Y-12N5.s2p") == "X-7Y-12"


def test_extract_xy_negative() -> None:
    assert extract_xy("X-3Y4N5.s2p") == (-3, 4)
    assert extract_xy("X+3Y-4N5.s2p") == (3, -4)


def test_extract_xy_returns_none_when_no_match() -> None:
    assert extract_xy("no_coords_here.s2p") == (None, None)
    assert extract_mark("no_mark_here.s2p") is None


def test_extract_port_case_insensitive() -> None:
    """大小写混合也得 match（实际文件名经常带 .S2P/.s1p 混用）。"""
    assert extract_port("foo_s11.s1p") == "S11"
    assert extract_port("foo_S22.s1p") == "S22"


def test_pf_flag_set_only_for_fail_keyword() -> None:
    assert parse_filename("foo_Fail.s2p").pf == "Y"
    assert parse_filename("foo_Pass.s2p").pf == "N"
    assert parse_filename("foo.s2p").pf == "N"
