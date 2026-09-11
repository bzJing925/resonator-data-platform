"""mason_sim.corpus 通用语料机械设施单元测试。

覆盖：config 指纹规范化、shard 原子写/合并回读（含额外诊断字段）、
断点续跑跳过、指纹不匹配中止、git SHA、meta 路径、原子写、CLI 数值校验。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
import pytest

from app.mason_sim import corpus

FIELDS = (
    corpus.ShardField("x", "x_geom", np.float64, 3),
    corpus.ShardField("y", "y_params", np.float64, 2),
    corpus.ShardField("z", "z_dense", np.complex64, 5),
    corpus.ShardField("diag", "diag", np.float64, 4),
)


def _result(i: int, valid: bool = True, reason: str = "ok") -> dict:
    if not valid:
        return {"valid": False, "reason": reason}
    return {
        "valid": True,
        "reason": "ok",
        "x": [float(i), float(i) + 0.1, float(i) + 0.2],
        "y": [float(i) * 1e-3, 1.0 + i],
        "z": (np.arange(5) + 1j * i).astype(np.complex128),
        "diag": [float(i), np.nan, 2.0, 3.0],
    }


def test_config_tag_canonical_order_independent() -> None:
    """同一 payload 不同键序 → 同一指纹；内容变化 → 指纹变化。"""
    tag_a = corpus.make_config_tag({"b": 1, "a": {"y": [1, 2], "x": 0.5}})
    tag_b = corpus.make_config_tag({"a": {"x": 0.5, "y": [1, 2]}, "b": 1})
    assert tag_a == tag_b
    assert json.loads(tag_a) == {"a": {"x": 0.5, "y": [1, 2]}, "b": 1}
    assert tag_a != corpus.make_config_tag({"a": {"x": 0.5, "y": [1, 2]}, "b": 2})


def test_write_shard_and_merge_roundtrip(tmp_path: Path) -> None:
    """写两个 shard 再合并：数组按序拼接、n_attempted 求和、丢弃计数聚合。"""
    tag = corpus.make_config_tag({"k": 1})
    corpus.write_shard(
        tmp_path / "shard_00000.npz",
        [_result(0), _result(1), _result(2, False, "no_resonance")],
        n_attempted=3,
        config_tag=tag,
        fields=FIELDS,
    )
    corpus.write_shard(
        tmp_path / "shard_00001.npz",
        [_result(3), _result(4, False, "label_nan")],
        n_attempted=2,
        config_tag=tag,
        fields=FIELDS,
    )

    merged = corpus.merge_shards(shard_dir=tmp_path, n_shards=2, config_tag=tag, fields=FIELDS)
    x = merged["arrays"]["x_geom"]
    y = merged["arrays"]["y_params"]
    z = merged["arrays"]["z_dense"]
    diag = merged["arrays"]["diag"]
    assert x.shape == (3, 3) and x.dtype == np.float64
    assert y.shape == (3, 2) and y.dtype == np.float64
    assert z.shape == (3, 5) and z.dtype == np.complex64
    assert diag.shape == (3, 4) and diag.dtype == np.float64
    np.testing.assert_array_equal(x[:, 0], [0.0, 1.0, 3.0])  # 顺序 = 样本序
    np.testing.assert_array_equal(z[1].real, np.arange(5))
    assert np.isnan(diag[0, 1])  # diag 允许 nan（TE1 诊断抑制语义）
    assert merged["n_attempted"] == 5
    assert merged["drop_counts"] == {"no_resonance": 1, "label_nan": 1}


def test_write_shard_all_dropped_produces_empty_arrays(tmp_path: Path) -> None:
    """全丢弃 shard：数组为 (0, width) 空壳，仍可合并。"""
    tag = corpus.make_config_tag({"k": 1})
    corpus.write_shard(
        tmp_path / "shard_00000.npz",
        [_result(0, False, "solver_error"), _result(1, False, "solver_error")],
        n_attempted=2,
        config_tag=tag,
        fields=FIELDS,
    )
    merged = corpus.merge_shards(shard_dir=tmp_path, n_shards=1, config_tag=tag, fields=FIELDS)
    assert merged["arrays"]["x_geom"].shape == (0, 3)
    assert merged["arrays"]["z_dense"].dtype == np.complex64
    assert merged["n_attempted"] == 2
    assert merged["drop_counts"] == {"solver_error": 2}


def test_shard_written_atomically_no_tmp_left(tmp_path: Path) -> None:
    """原子写：shard 落盘后目录无 .tmp 残留。"""
    tag = corpus.make_config_tag({"k": 1})
    corpus.write_shard(tmp_path / "shard_00000.npz", [_result(0)], 1, tag, FIELDS)
    assert not list(tmp_path.glob("*.tmp"))


def test_check_shard_tag_mismatch_aborts(tmp_path: Path) -> None:
    """配置指纹不匹配 → SystemExit（不静默混料/重算）。"""
    corpus.write_shard(
        tmp_path / "shard_00000.npz",
        [_result(0)],
        1,
        corpus.make_config_tag({"k": 1}),
        FIELDS,
    )
    with pytest.raises(SystemExit, match="配置指纹不匹配"):
        corpus.check_shard_tag(tmp_path / "shard_00000.npz", corpus.make_config_tag({"k": 2}))
    # 无 config_tag 的旧 shard 同样中止
    bad = tmp_path / "shard_00009.npz"
    np.savez(bad, x_geom=np.zeros((1, 3)))
    with pytest.raises(SystemExit, match="配置指纹不匹配"):
        corpus.check_shard_tag(bad, corpus.make_config_tag({"k": 1}))


def test_merge_shards_missing_shard_raises(tmp_path: Path) -> None:
    """合并时缺 shard → FileNotFoundError（求解不完整不得产出）。"""
    with pytest.raises(FileNotFoundError, match="缺少 shard"):
        corpus.merge_shards(shard_dir=tmp_path, n_shards=1, config_tag="t", fields=FIELDS)


def _toy_solve(row: tuple[float, ...]) -> dict:
    """测试用玩具求解器：偶数 index 有效，奇数丢弃（确定性，可进程内序列跑）。"""
    i = int(row[0])
    if i % 2:
        return {"valid": False, "reason": "odd_drop"}
    return {
        "valid": True,
        "reason": "ok",
        "x": [float(i), 0.0, 1.0],
        "y": [float(i), 2.0],
        "z": np.full(5, i, dtype=np.complex64),
        "diag": [float(i), 1.0, 2.0, 3.0],
    }


def test_solve_shards_serial_and_resume(tmp_path: Path) -> None:
    """nprocs=1 序列求解 → shard 落盘；回拨 mtime 续跑不重算且合并内容不变。"""
    rows = [(float(i),) for i in range(7)]
    tag = corpus.make_config_tag({"n": 7})
    n_shards = corpus.solve_shards(
        rows=rows,
        solve_one=_toy_solve,
        shard_dir=tmp_path,
        shard_size=3,
        nprocs=1,
        config_tag=tag,
        fields=FIELDS,
        log=lambda _msg: None,
    )
    assert n_shards == 3  # 7 / 3 → 3 shards
    shards = sorted(tmp_path.glob("shard_*.npz"))
    assert len(shards) == 3

    merged1 = corpus.merge_shards(shard_dir=tmp_path, n_shards=3, config_tag=tag, fields=FIELDS)
    assert merged1["n_attempted"] == 7
    assert merged1["drop_counts"] == {"odd_drop": 3}
    np.testing.assert_array_equal(merged1["arrays"]["x_geom"][:, 0], [0.0, 2.0, 4.0, 6.0])

    for p in shards:
        os.utime(p, (0, 0))
    corpus.solve_shards(
        rows=rows,
        solve_one=_toy_solve,
        shard_dir=tmp_path,
        shard_size=3,
        nprocs=1,
        config_tag=tag,
        fields=FIELDS,
        log=lambda _msg: None,
    )
    for p in shards:
        assert p.stat().st_mtime == 0, f"shard 被重算：{p}"
    merged2 = corpus.merge_shards(shard_dir=tmp_path, n_shards=3, config_tag=tag, fields=FIELDS)
    np.testing.assert_array_equal(merged2["arrays"]["x_geom"], merged1["arrays"]["x_geom"])


def test_solve_shards_tag_mismatch_aborts(tmp_path: Path) -> None:
    """续跑时既有 shard 指纹不符 → SystemExit。"""
    rows = [(0.0,), (1.0,)]
    corpus.solve_shards(
        rows=rows,
        solve_one=_toy_solve,
        shard_dir=tmp_path,
        shard_size=2,
        nprocs=1,
        config_tag=corpus.make_config_tag({"n": 2}),
        fields=FIELDS,
        log=lambda _msg: None,
    )
    with pytest.raises(SystemExit, match="配置指纹不匹配"):
        corpus.solve_shards(
            rows=rows,
            solve_one=_toy_solve,
            shard_dir=tmp_path,
            shard_size=2,
            nprocs=1,
            config_tag=corpus.make_config_tag({"n": 999}),
            fields=FIELDS,
            log=lambda _msg: None,
        )


def test_git_sha_format() -> None:
    """git_sha：40 位十六进制，允许 -dirty 后缀。"""
    sha = corpus.git_sha(Path(__file__).resolve().parents[2])
    base = sha.removesuffix("-dirty")
    assert len(base) == 40 and all(c in "0123456789abcdef" for c in base)


def test_meta_path_for_derivation() -> None:
    """meta 路径派生：.npz → .meta.json；非 .npz 报错。"""
    assert corpus.meta_path_for(Path("/x/corpus.npz")) == Path("/x/corpus.meta.json")
    with pytest.raises(ValueError, match="npz"):
        corpus.meta_path_for(Path("/x/corpus.txt"))


def test_atomic_savez_and_json(tmp_path: Path) -> None:
    """原子写 npz/json：内容可读、无 .tmp 残留。"""
    out = tmp_path / "c.npz"
    corpus.atomic_savez(
        out,
        {"x_geom": np.ones((2, 3)), "z_dense": np.ones((2, 5), dtype=np.complex64)},
        compressed=True,
    )
    with np.load(out) as d:
        assert d["x_geom"].shape == (2, 3)
        assert d["z_dense"].dtype == np.complex64

    meta = tmp_path / "c.meta.json"
    corpus.atomic_write_json(meta, {"a": 1, "中文": "值"})
    assert json.loads(meta.read_text()) == {"a": 1, "中文": "值"}
    assert not list(tmp_path.glob("*.tmp"))


def test_validate_positive_ints() -> None:
    """CLI 数值校验：非正整数 → SystemExit(code=2)；正数通过。"""
    ap = argparse.ArgumentParser()
    corpus.validate_positive_ints(ap, {"--n": 5, "--nprocs": 1})  # 不抛
    for bad in (0, -3):
        with pytest.raises(SystemExit) as exc_info:
            corpus.validate_positive_ints(ap, {"--n": bad})
        assert exc_info.value.code == 2
