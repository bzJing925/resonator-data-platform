"""临时生成平台流程测试包；生成的ZIP仅写入调用方提供的tmp_path。"""

from __future__ import annotations

import importlib
import sys
import warnings
import zipfile
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID


def _algorithm_samples(tmp_path: Path) -> dict[str, bytes]:
    """复用现有处理器算法测试中的有效 S1P/S2P 共振样本生成器。"""
    tests_root = str(Path(__file__).parents[1])
    inserted = tests_root not in sys.path
    if inserted:
        sys.path.insert(0, tests_root)
    try:
        generator = importlib.import_module("workers.pipeline.test_processor")
    finally:
        if inserted:
            sys.path.remove(tests_root)

    sample_dir = tmp_path / "algorithm-samples"
    paths = {
        "dut_s2p_a": sample_dir / "dut-a.s2p",
        "dut_s2p_b": sample_dir / "dut-b.s2p",
        "dut_s1p": sample_dir / "dut.s1p",
        "cal_open": sample_dir / "cal-open.s2p",
        "cal_short": sample_dir / "cal-short.s2p",
    }
    generator._write_minimal_s2p_with_resonance(paths["dut_s2p_a"])
    generator._write_minimal_s2p_with_resonance(paths["dut_s2p_b"], n_points=181)
    generator._write_minimal_s1p_with_resonance(paths["dut_s1p"])
    generator._write_minimal_s2p_with_resonance(paths["cal_open"])
    generator._write_minimal_s2p_with_resonance(paths["cal_short"])
    return {name: path.read_bytes() for name, path in paths.items()}


def _write_archive(path: Path, members: list[tuple[str, bytes]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, payload in members:
                archive.writestr(name, payload)
    return path


def make_platform_archives(tmp_path: Path) -> dict[str, Path]:
    """返回B00要求的有效、损坏、重复成员、缺校准和部分失败临时ZIP。"""
    samples = _algorithm_samples(tmp_path)
    corrupt = b"# Hz S RI R 50\nthis is not a Touchstone row\n"
    return {
        "complete": _write_archive(
            tmp_path / "complete.zip",
            [
                ("wafer/17_E6-1_X0Y0.s2p", samples["dut_s2p_a"]),
                ("wafer/18_E6-2_X1Y0.s2p", samples["dut_s2p_b"]),
                ("wafer/19_E6-3_X2Y0.s1p", samples["dut_s1p"]),
                ("wafer/cal_OPEN.s2p", samples["cal_open"]),
                ("wafer/cal_SHORT.s2p", samples["cal_short"]),
                ("notes/readme.txt", b"ignored non-measurement file\n"),
            ],
        ),
        "duplicate_member": _write_archive(
            tmp_path / "duplicate-member.zip",
            [
                ("wafer/17_E6-1_X0Y0.s2p", samples["dut_s2p_a"]),
                ("wafer/17_E6-1_X0Y0.s2p", samples["dut_s2p_a"]),
            ],
        ),
        "corrupt_touchstone": _write_archive(
            tmp_path / "corrupt-touchstone.zip",
            [("wafer/17_E6-1_X0Y0.s1p", corrupt)],
        ),
        "missing_calibration": _write_archive(
            tmp_path / "missing-calibration.zip",
            [("wafer/17_E6-1_X0Y0.s2p", samples["dut_s2p_a"])],
        ),
        "partial_failure": _write_archive(
            tmp_path / "partial-failure.zip",
            [
                ("wafer/17_E6-1_X0Y0.s2p", samples["dut_s2p_a"]),
                ("wafer/18_E6-2_X1Y0.s1p", corrupt),
                ("notes/readme.txt", b"not a measurement file\n"),
            ],
        ),
    }


def same_name_source_refs() -> tuple[dict[str, object], dict[str, object]]:
    """两个显示名相同、永久来源UUID不同的批次引用。"""
    origin_a = str(UUID("10000000-0000-4000-8000-000000000001"))
    origin_b = str(UUID("20000000-0000-4000-8000-000000000002"))
    batch_a = str(UUID("30000000-0000-4000-8000-000000000003"))
    batch_b = str(UUID("40000000-0000-4000-8000-000000000004"))
    return (
        {"display_name": "lot-01", "origin_id": origin_a, "source_batch_id": batch_a},
        {"display_name": "lot-01", "origin_id": origin_b, "source_batch_id": batch_b},
    )


def large_query_rows(size: int = 200_001) -> Iterator[dict[str, int]]:
    """惰性生成行数上限边界数据，不在测试中一次性占用内存。"""
    for index in range(size):
        yield {"id": index + 1, "value": index % 997}
