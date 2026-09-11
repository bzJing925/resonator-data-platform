"""eval_forward 集成测试（TDD 先于实现）。

覆盖复审项：
- --data 透传到两阶段（重算路径同来源）
- 稀疏重建 k 从 stage1 ckpt 读（DEFAULT_CFG 无 k 时重算仍成功）
- checkpoint↔数据集 sha256 绑定：不匹配/缺字段 → warnings（stderr + json 字段）
- seed 从 stage1 config 读（不硬编码）
- anchor_sensitivity_rmse_db 键（乘性、± 两方向）替代旧 aligned 键
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
for p in (ROOT, SCRIPTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from mason_surrogate import eval_forward as ef  # noqa: E402
from mason_surrogate import train_stage1_mason as ts1  # noqa: E402
from mason_surrogate import train_stage2_mason as ts2  # noqa: E402

from tests.mason_surrogate.test_train_smoke import (  # noqa: E402
    _make_tiny_unified,
    _stage1_cfg,
    _stage2_cfg,
)


def _minimal_meta(data_path: Path) -> dict:
    """tiny 数据集的最小 meta（build_forward_eval 需要数据集统计口径）。"""
    with np.load(data_path) as d:
        struct = d["x7"][:, 0].astype(int)
        split = d["split"]
    counts = {}
    for flag, name in [(0, "epe"), (1, "eppe")]:
        sel = struct == flag
        counts[name] = {
            "train": int(((split == 0) & sel).sum()),
            "val": int(((split == 1) & sel).sum()),
            "test": int(((split == 2) & sel).sum()),
        }
    return {
        "w_grid": {"w_lo": 0.8, "w_hi": 1.2, "n": 1001, "step": 4e-4},
        "sources": {
            name: {"sha256": "x", "mason_sim_commit_sha": None, "n_in": int((struct == f).sum()),
                   "n_kept": int((struct == f).sum()), "n_dropped_zero_coverage": 0,
                   "mask_rate": 0.0}
            for f, name in [(0, "epe"), (1, "eppe")]
        },
        "split": {"counts": counts},
    }


@pytest.fixture(scope="module")
def trained_ckpt(tmp_path_factory):
    """tiny 数据上训练 stage1+stage2 到同一 ckpt 目录；删除 *_eval.json 强制重算路径。"""
    tmp = tmp_path_factory.mktemp("fw")
    data = _make_tiny_unified(tmp / "unified.npz")
    ckpt = tmp / "ckpt"
    ts1.train(_stage1_cfg(data, ckpt))
    ts2.train(_stage2_cfg(data, ckpt, ckpt))
    for name in ("stage1_eval.json", "stage2_eval.json"):
        p = ckpt / name
        if p.exists():
            p.unlink()
    return {"data": data, "ckpt": ckpt, "meta": _minimal_meta(data)}


def test_build_forward_eval_happy_path(trained_ckpt):
    out, warnings = ef.build_forward_eval(
        trained_ckpt["ckpt"], trained_ckpt["data"], trained_ckpt["meta"]
    )
    assert warnings == []
    assert out["seed"] == 20260911  # 从 stage1 config 读，非硬编码
    assert out["dataset"]["dataset_sha256"] == ef.file_sha256(trained_ckpt["data"])
    s1 = out["stage1"]["per_structure"]["all"]
    assert "fs_mae_mhz" in s1 and "sparse_rmse_db" in s1
    s2 = out["stage2"]["per_structure"]["all"]
    assert "full_rmse_db" in s2 and "zp_peak_mae_db" in s2
    assert "aligned_rmse_db" not in s2  # 旧键已更名
    sens = s2["anchor_sensitivity_rmse_db"]
    assert set(sens) == {"plus", "minus"}
    assert out["stage2"]["anchor_sensitivity_note"]


def test_build_forward_eval_warns_on_dataset_mismatch(trained_ckpt, tmp_path):
    # 复制 ckpt，破坏 sha 绑定：stage1 改错、stage2 删字段
    ckpt2 = tmp_path / "ckpt_bad"
    shutil.copytree(trained_ckpt["ckpt"], ckpt2)
    c1 = json.loads((ckpt2 / "stage1_config.json").read_text())
    c1["dataset_sha256"] = "0" * 64
    (ckpt2 / "stage1_config.json").write_text(json.dumps(c1))
    c2 = json.loads((ckpt2 / "stage2_config.json").read_text())
    del c2["dataset_sha256"]
    (ckpt2 / "stage2_config.json").write_text(json.dumps(c2))

    out, warnings = ef.build_forward_eval(ckpt2, trained_ckpt["data"], trained_ckpt["meta"])
    assert len(warnings) == 2
    assert any("stage1" in w and "不一致" in w for w in warnings)
    assert any("stage2" in w and "缺" in w for w in warnings)
    assert out["warnings"] == warnings  # 写进 json 字段


def test_main_prints_warnings_to_stderr(trained_ckpt, tmp_path, monkeypatch, capsys):
    ckpt2 = tmp_path / "ckpt_bad2"
    shutil.copytree(trained_ckpt["ckpt"], ckpt2)
    c1 = json.loads((ckpt2 / "stage1_config.json").read_text())
    c1["dataset_sha256"] = "f" * 64
    (ckpt2 / "stage1_config.json").write_text(json.dumps(c1))
    meta_path = tmp_path / "meta.json"
    meta_path.write_text(json.dumps(trained_ckpt["meta"]))
    out_path = tmp_path / "fw.json"
    monkeypatch.setattr(
        sys, "argv",
        ["eval_forward.py", "--ckpt", str(ckpt2), "--data", str(trained_ckpt["data"]),
         "--meta", str(meta_path), "--out", str(out_path)],
    )
    ef.main()
    assert "WARNING" in capsys.readouterr().err
    assert out_path.exists()
