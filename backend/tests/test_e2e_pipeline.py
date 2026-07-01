"""端到端 pipeline 烟雾测试：T8901P.01.zip → 24 字段输出。

不与客户脚本做位对位比对（按用户决策放弃 baseline），只验证：
1. 整个 pipeline 跑通
2. 输出字段数符合 ResonatorRow 契约
3. 数值在物理合理范围
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pandas as pd
import pytest

from app.core.extract import extract_resonator_params
from app.core.mapping import load_mapping
from app.core.touchstone import split_s2p_to_s1p


@pytest.fixture(scope="module")
def workdir(tmp_path_factory: pytest.TempPathFactory, sample_zip: Path) -> Path:
    """解压样例 zip 到临时目录。"""
    work = tmp_path_factory.mktemp("e2e")
    with zipfile.ZipFile(sample_zip) as zf:
        zf.extractall(work)
    return work


def test_pipeline_runs_on_t8901p_01(workdir: Path, sample_mapping: Path) -> None:
    """跑通 zip → DUT × 2 端口 → ResonatorRow。"""
    mapping = load_mapping(sample_mapping)
    assert len(mapping) > 0, "mapping 加载失败"

    # fixture zip 解压后 .s2p 直接在根目录（共 15 个）
    s2p_files = sorted(workdir.rglob("*.s2p"))
    assert s2p_files, f"未找到 .s2p 文件，workdir={workdir}"

    rows = []
    failures = []
    for s2p in s2p_files:
        split = split_s2p_to_s1p(
            s2p,
            out_dir_s11=workdir / "S11",
            out_dir_s22=workdir / "S22",
        )
        for s1p_path, port in [(split.s11_path, "S11"), (split.s22_path, "S22")]:
            try:
                row = extract_resonator_params(
                    s1p_path,
                    mapping=mapping,
                    wafer=1,
                    s_param_relpath=str(s1p_path.relative_to(workdir)),
                )
                rows.append(row)
            except Exception as exc:
                failures.append(f"{s1p_path.name} ({port}): {exc}")

    print(f"\n成功: {len(rows)} 行，失败: {len(failures)}")
    for f in failures:
        print(f"  ❌ {f}")

    assert len(rows) >= 12, f"至少应跑通一半（12 行），实际 {len(rows)} 行"

    df = pd.DataFrame([r.model_dump() for r in rows])
    print(f"\n输出 DataFrame shape: {df.shape}")
    print(f"列: {list(df.columns)}")
    cols = ["original_filename", "folder_name", "mark", "fs_ghz", "fp_ghz", "qs", "qp", "k2eff_pct"]
    print(df[cols].head(10).to_string())

    assert df["fs_ghz"].between(1, 30).all(), "fs 物理范围异常"
    assert df["fp_ghz"].between(1, 30).all(), "fp 物理范围异常"
    assert (df["fs_ghz"] < df["fp_ghz"]).all(), "fs 应 < fp"
    assert df["k2eff_pct"].between(0, 50).all(), "k2eff 物理范围异常"
    assert df["qs"].between(0, 100000).all(), "Qs 物理范围异常"

    out_csv = workdir / "result.csv"
    df.to_csv(out_csv, index=False)
    print(f"\n结果已写入: {out_csv}")

    # 失败率 < 20% 视为正常（数据本身可能有 fs/fp 反序、测量异常等情况）
    total = len(rows) + len(failures)
    fail_rate = len(failures) / total
    assert fail_rate < 0.2, f"失败率过高: {fail_rate:.1%}"
