"""pytest 全局 fixture。"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def sample_zip(fixtures_dir: Path) -> Path:
    """T8901P.01.zip — 12 个 DUT 样例。"""
    return fixtures_dir / "T8901P.01.zip"


@pytest.fixture(scope="session")
def sample_mapping(fixtures_dir: Path) -> Path:
    """mapping_ELB003.xlsx — 749 行对照表。"""
    return fixtures_dir / "mapping_ELB003.xlsx"
