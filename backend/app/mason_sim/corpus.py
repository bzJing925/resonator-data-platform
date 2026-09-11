"""Mason 语料生成的通用机械设施（IUS2026 WP2b 抽取自 WP2a EPE 语料脚本）。

只承载几何无关的工程设施，供 build_mason_epe_corpus.py / build_mason_eppe_corpus.py
等语料脚本复用：

- ``make_config_tag`` / ``check_shard_tag``：语料配置指纹（canonical JSON）。
  凡影响 shard 内容的量（seed/n/material/shard_size/厚度范围/求解窗/材料来源等）
  都必须计入 payload；断点续跑只许"同配置续跑"，指纹不匹配即 SystemExit 中止
  （防止不同配置的 shard 静默混料）。
- ``write_shard`` / ``solve_shards`` / ``merge_shards``：shard 原子写
  （tmp + os.replace）、断点续跑求解循环（已存在 shard 校验指纹后跳过）、
  分片合并（数组拼接 + n_attempted 求和 + 丢弃计数聚合）。
- ``atomic_savez`` / ``atomic_write_json``：最终产物原子写。
- ``git_sha`` / ``meta_path_for`` / ``validate_positive_ints``：meta 配套工具。

单样本结果 dict 约定（语料脚本的 solve_extract_one 返回）：
- 有效：{"valid": True, "reason": "ok", <field.result_key>: 等长 1D 数组值...}
- 无效：{"valid": False, "reason": <丢弃原因 str>}（可选 "x"/"error" 诊断字段，
  不落盘）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SHARD_NAME_FMT = "shard_{idx:05d}.npz"


@dataclass(frozen=True)
class ShardField:
    """单个数组字段的 shard/合并规格。

    Attributes:
        result_key: 单样本 result dict 中承载该字段值的键
        npz_key: shard 与最终 npz 中的数组键
        dtype: 数组 dtype（空 shard 时用于构造 (0, width) 空壳）
        width: 每样本长度（1D）
    """

    result_key: str
    npz_key: str
    dtype: np.dtype
    width: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "dtype", np.dtype(self.dtype))


# ============================================================
# 配置指纹
# ============================================================
def make_config_tag(payload: Mapping) -> str:
    """语料配置指纹：canonical JSON（键排序，保证键序无关的同值同指纹）。"""
    return json.dumps(payload, sort_keys=True)


def check_shard_tag(shard_path: Path, expected_tag: str) -> None:
    """校验已存在 shard 的配置指纹；不匹配即 SystemExit 中止（不静默混料/重算）。"""
    with np.load(shard_path) as d:
        stored = str(d["config_tag"]) if "config_tag" in d else None
    if stored != expected_tag:
        raise SystemExit(
            f"shard 配置指纹不匹配：{shard_path}\n"
            "该 shard 由不同配置（seed/n/material/shard_size/范围/求解窗等）生成，"
            "为避免静默混料已中止。请更换 --shard-dir 或清空该目录后重跑。"
        )


# ============================================================
# shard 写 / 求解循环 / 合并
# ============================================================
def write_shard(
    shard_path: Path,
    results: list[dict],
    n_attempted: int,
    config_tag: str,
    fields: Sequence[ShardField],
) -> None:
    """把一个 shard 的有效样本写入 npz（tmp + os.replace 原子替换）。

    shard 内含 config_tag（配置指纹）与 drop_counts_json（丢弃原因计数）。
    全丢弃时各字段写 (0, width) 空壳数组（保持 schema 可合并）。
    """
    valid = [r for r in results if r["valid"]]
    drop_counts: dict[str, int] = {}
    for r in results:
        if not r["valid"]:
            drop_counts[r["reason"]] = drop_counts.get(r["reason"], 0) + 1

    arrays: dict[str, np.ndarray] = {}
    for field in fields:
        if valid:
            stacked = np.stack([np.asarray(r[field.result_key]) for r in valid]).astype(field.dtype)
        else:
            stacked = np.empty((0, field.width), dtype=field.dtype)
        arrays[field.npz_key] = stacked

    shard_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = shard_path.with_suffix(".tmp")
    with open(tmp, "wb") as fh:  # 文件句柄写入，避免 savez 自动追加 .npz 后缀
        np.savez(
            fh,
            **arrays,
            n_attempted=np.int64(n_attempted),
            drop_counts_json=np.str_(json.dumps(drop_counts, sort_keys=True)),
            config_tag=np.str_(config_tag),
        )
    os.replace(tmp, shard_path)


def solve_shards(
    *,
    rows: Sequence[tuple],
    solve_one: Callable[[tuple], dict],
    shard_dir: Path,
    shard_size: int,
    nprocs: int,
    config_tag: str,
    fields: Sequence[ShardField],
    log: Callable[[str], None] = print,
) -> int:
    """断点续跑求解循环：逐 shard 求解（已存在 shard 校验指纹后跳过）并落盘。

    nprocs=1 时进程内序列求解（测试/调试友好）；nprocs>1 用 ProcessPoolExecutor，
    solve_one 必须可 pickle（顶层函数的 functools.partial 等）。

    Returns:
        n_shards（供 merge_shards 使用）。
    """
    n = len(rows)
    n_shards = (n + shard_size - 1) // shard_size

    def _solve_chunk(shard_idx: int, shard_path: Path, exe: ProcessPoolExecutor | None) -> None:
        lo = shard_idx * shard_size
        hi = min(n, lo + shard_size)
        chunk = list(rows[lo:hi])
        if exe is not None:
            chunksize = max(1, len(chunk) // (nprocs * 4))
            results = list(exe.map(solve_one, chunk, chunksize=chunksize))
        else:
            results = [solve_one(row) for row in chunk]
        write_shard(
            shard_path, results, n_attempted=len(chunk), config_tag=config_tag, fields=fields
        )
        n_ok = sum(1 for r in results if r["valid"])
        log(f"[shard {shard_idx + 1}/{n_shards}] 求解 {len(chunk)} 样本：有效 {n_ok}")

    if nprocs > 1:
        with ProcessPoolExecutor(max_workers=nprocs) as exe:
            for shard_idx in range(n_shards):
                shard_path = shard_dir / SHARD_NAME_FMT.format(idx=shard_idx)
                if shard_path.exists():
                    check_shard_tag(shard_path, config_tag)  # 指纹不符立即中止
                    log(f"[shard {shard_idx + 1}/{n_shards}] 已存在，跳过（断点续跑）")
                    continue
                _solve_chunk(shard_idx, shard_path, exe)
    else:
        for shard_idx in range(n_shards):
            shard_path = shard_dir / SHARD_NAME_FMT.format(idx=shard_idx)
            if shard_path.exists():
                check_shard_tag(shard_path, config_tag)
                log(f"[shard {shard_idx + 1}/{n_shards}] 已存在，跳过（断点续跑）")
                continue
            _solve_chunk(shard_idx, shard_path, None)
    return n_shards


def merge_shards(
    *,
    shard_dir: Path,
    n_shards: int,
    config_tag: str,
    fields: Sequence[ShardField],
) -> dict:
    """合并 shards：校验指纹 → 数组按 shard 序拼接 → 聚合 n_attempted 与丢弃计数。

    Returns:
        {"arrays": {npz_key: 拼接后数组}, "n_attempted": int,
         "drop_counts": {reason: count}}。drop_counts 只含观测到的原因；
         需要固定键集合时由调用方补零。
    """
    parts: dict[str, list[np.ndarray]] = {f.npz_key: [] for f in fields}
    n_attempted = 0
    drop_counts: dict[str, int] = {}
    for shard_idx in range(n_shards):
        shard_path = shard_dir / SHARD_NAME_FMT.format(idx=shard_idx)
        if not shard_path.exists():
            raise FileNotFoundError(f"缺少 shard：{shard_path}（求解不完整）")
        check_shard_tag(shard_path, config_tag)  # 合并时复核（防御预置异配置 shard）
        with np.load(shard_path) as d:
            for field in fields:
                parts[field.npz_key].append(np.asarray(d[field.npz_key]))
            n_attempted += int(d["n_attempted"])
            for reason, count in json.loads(str(d["drop_counts_json"])).items():
                drop_counts[reason] = drop_counts.get(reason, 0) + int(count)

    arrays: dict[str, np.ndarray] = {}
    for field in fields:
        chunks = parts[field.npz_key]
        if any(len(a) for a in chunks):
            arrays[field.npz_key] = np.concatenate(chunks).astype(field.dtype)
        else:
            arrays[field.npz_key] = np.empty((0, field.width), dtype=field.dtype)
    return {"arrays": arrays, "n_attempted": n_attempted, "drop_counts": drop_counts}


# ============================================================
# 最终产物原子写
# ============================================================
def atomic_savez(out_path: Path, arrays: Mapping[str, np.ndarray], *, compressed: bool) -> None:
    """npz 原子写（tmp + os.replace，避免中断留下半截文件）。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp")
    savez = np.savez_compressed if compressed else np.savez
    with open(tmp, "wb") as fh:  # 文件句柄写入，避免 savez 自动追加 .npz 后缀
        savez(fh, **dict(arrays))
    os.replace(tmp, out_path)


def atomic_write_json(path: Path, payload: Mapping) -> None:
    """JSON 原子写（UTF-8，ensure_ascii=False，indent=2）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    os.replace(tmp, path)


# ============================================================
# 杂项
# ============================================================
def git_sha(project_root: Path) -> str:
    """当前 commit SHA；工作区不干净（git status --porcelain 非空）附 -dirty。"""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return sha + ("-dirty" if dirty else "")
    except Exception:
        return "unknown"


def meta_path_for(out_path: Path) -> Path:
    """corpus npz 路径 → 配套 meta.json 路径（仅接受 .npz 后缀）。"""
    if out_path.suffix != ".npz":
        raise ValueError(f"输出路径必须是 .npz 文件：{out_path}")
    return out_path.with_suffix(".meta.json")


def validate_positive_ints(ap: argparse.ArgumentParser, values: Mapping[str, int]) -> None:
    """CLI 数值校验：非正整数 → ap.error（人类可读报错，exit code 2）。"""
    for name, value in values.items():
        if value <= 0:
            ap.error(f"{name} 必须为正整数（收到 {value}）")
