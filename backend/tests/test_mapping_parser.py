"""对照表 Description token 解析边界。"""

from __future__ import annotations

from app.core.mapping import (
    MappingEntry,
    get_area_for_port,
    parse_description,
)


def test_parse_typical() -> None:
    e = parse_description("A1-1", "EG0 FL0 700&5500")
    assert e.eg == 0.0
    assert e.fl == 0.0
    assert e.ag is None
    assert e.area_s11 == 700
    assert e.area_s22 == 5500
    assert e.has_pf is False


def test_parse_with_decimal_and_ag() -> None:
    e = parse_description("A2-3", "EG0 FL0.5 AG1.2 1200&4500")
    assert e.eg == 0.0
    assert e.fl == 0.5
    assert e.ag == 1.2
    assert e.area_s11 == 1200
    assert e.area_s22 == 4500


def test_parse_pf_via_plus_pf() -> None:
    e = parse_description("D1-1", "EG0+PF FL0 900&5000")
    assert e.has_pf is True


def test_parse_pf_via_standalone_token() -> None:
    e = parse_description("D2-1", "EG0 FL0 PF 900&5000")
    assert e.has_pf is True


def test_parse_no_area_pair() -> None:
    """没 'N&M' 模式时 area 应是 None。"""
    e = parse_description("X1-1", "EG1 FL2")
    assert e.area_s11 is None
    assert e.area_s22 is None


def test_get_area_for_port() -> None:
    e = MappingEntry(mark="X", description="x", area_s11=100, area_s22=200)
    assert get_area_for_port(e, "S11") == 100
    assert get_area_for_port(e, "S22") == 200
    # 大小写无关
    assert get_area_for_port(e, "s11") == 100
    # 未知 port 走 else 分支默认 S22
    assert get_area_for_port(e, "FOO") == 200


def test_parse_returns_raw_tokens() -> None:
    e = parse_description("Z", "EG0 FL0 700&5500 PF")
    assert e.raw_tokens == ("EG0", "FL0", "700&5500", "PF")
