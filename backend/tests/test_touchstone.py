"""S2P 拆分边界。

历史 bug：_extract_s11 接受 ≥3 列、_extract_s22 接受 ≥9 列，遇到列数 3-8 的损坏行
时 S11 会保留、S22 会丢弃，产生 frequency 轴对不上的两个 .s1p 文件，下游 skrf
de-embed 静默给出错位的结果。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.touchstone import split_s2p_to_s1p

_GOOD_HEADER = "! comment\n# Hz S MA R 50\n"
_GOOD_DATA_ROW = (
    "1.0e9 0.1 -0.2 0.3 -0.4 0.5 -0.6 0.7 -0.8\n"
)


def _make_s2p(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "fake.s2p"
    p.write_text(body)
    return p


def test_extract_drops_blank_lines(tmp_path: Path) -> None:
    """末尾的空行/换行是常见的，应当被静默跳过。"""
    body = _GOOD_HEADER + _GOOD_DATA_ROW * 3 + "\n\n"
    s2p = _make_s2p(tmp_path, body)
    res = split_s2p_to_s1p(s2p, tmp_path / "s11", tmp_path / "s22")
    s11_lines = [ln for ln in res.s11_path.read_text().splitlines() if ln.strip()]
    s22_lines = [ln for ln in res.s22_path.read_text().splitlines() if ln.strip()]
    # 关键是 S11/S22 行数要一致（核心不变量）。
    assert len(s11_lines) == len(s22_lines)


def test_extract_raises_on_partial_row(tmp_path: Path) -> None:
    """3 列的数据行：旧实现 S11 收下、S22 丢掉，造成对不上。修复后应 raise。"""
    body = _GOOD_HEADER + _GOOD_DATA_ROW * 2 + "2.0e9 0.1 -0.2\n" + _GOOD_DATA_ROW
    s2p = _make_s2p(tmp_path, body)
    with pytest.raises(ValueError, match="列数 3 < 9"):
        split_s2p_to_s1p(s2p, tmp_path / "s11", tmp_path / "s22")


def test_extract_raises_on_8col_row(tmp_path: Path) -> None:
    """8 列（缺最后一列）—— 同样会让 S22 拿错列；必须 raise。"""
    body = _GOOD_HEADER + _GOOD_DATA_ROW + "2.0e9 0.1 -0.2 0.3 -0.4 0.5 -0.6 0.7\n"
    s2p = _make_s2p(tmp_path, body)
    with pytest.raises(ValueError, match="列数 8 < 9"):
        split_s2p_to_s1p(s2p, tmp_path / "s11", tmp_path / "s22")
