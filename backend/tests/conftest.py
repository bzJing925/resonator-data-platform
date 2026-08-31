"""pytest 全局 fixture。"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def sample_zip(fixtures_dir: Path) -> Path:
    """T8901P.01.zip — 12 个 DUT 样例。若真实 fixture 不存在则跳过依赖它的测试。"""
    path = fixtures_dir / "T8901P.01.zip"
    if not path.exists():
        pytest.skip(f"{path} 不存在，跳过需要真实样例 zip 的测试")
    return path


@pytest.fixture(scope="session")
def sample_mapping(fixtures_dir: Path) -> Path:
    """mapping_ELB003.xlsx — 749 行对照表。

    若真实 fixture 不存在（客户材料未同步），在临时目录生成一个最小可用对照表，
    让依赖该 fixture 的测试能跑起来。
    """
    real = fixtures_dir / "mapping_ELB003.xlsx"
    if real.exists():
        return real

    import tempfile

    import pandas as pd

    tmp = Path(tempfile.gettempdir()) / "aln_test_mapping_ELB003.xlsx"
    df = pd.DataFrame(
        {
            0: ["E6-1", "E6-2"],
            1: ["EG0 FL0 700&5500", "EG0 FL0.5 1200&4500"],
        }
    )
    df.to_excel(tmp, index=False, header=False)
    return tmp
