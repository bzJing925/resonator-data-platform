"""WP4b 数据效率（run_data_efficiency）的单元测试（TDD 先于实现）。

覆盖：
- stratified_subset：分层比例/最少每类 1 个/确定性/n=全集/超量报错
- build_curve_rows / write_subset_npz：子集只来自 EPPE train split，无 test 泄漏，
  npz 往返后 split 标签与源数组一致
- collect_stage1_metrics / collect_stage2_metrics：从 eval json 提取指标
- write_xlsx：sheet 名、表头、行数、readme 口径说明
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
for p in (ROOT, SCRIPTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from mason_surrogate import run_data_efficiency as rde  # noqa: E402

CLASSES = np.array([50.0, 80.0, 100.0, 120.0, 150.0])


def test_stratified_subset_balanced():
    labels = np.repeat(CLASSES, 100)
    labels = np.random.default_rng(0).permutation(labels)
    sel = rde.stratified_subset(labels, 50, seed=1)
    assert len(sel) == 50
    for c in CLASSES:
        assert int((labels[sel] == c).sum()) == 10
    sel10 = rde.stratified_subset(labels, 10, seed=1)
    assert len(sel10) == 10
    for c in CLASSES:
        assert int((labels[sel10] == c).sum()) == 2


def test_stratified_subset_unbalanced_min_per_class():
    labels = np.concatenate(
        [np.full(200, 50.0), np.full(100, 80.0), np.full(100, 100.0),
         np.full(50, 120.0), np.full(50, 150.0)]
    )
    sel = rde.stratified_subset(labels, 50, seed=2)
    assert len(sel) == 50
    assert int((labels[sel] == 50.0).sum()) == 20  # 精确比例
    sel_small = rde.stratified_subset(labels, 25, seed=2)
    assert len(sel_small) == 25
    for c in CLASSES:
        assert int((labels[sel_small] == c).sum()) >= 2  # 最少每类有保证


def test_stratified_subset_determinism():
    labels = np.repeat(CLASSES, 60)
    a = rde.stratified_subset(labels, 30, seed=42)
    b = rde.stratified_subset(labels, 30, seed=42)
    c = rde.stratified_subset(labels, 30, seed=43)
    assert np.array_equal(a, b)
    assert not np.array_equal(a, c)


def test_stratified_subset_full_and_overflow():
    labels = np.repeat(CLASSES, 20)
    sel = rde.stratified_subset(labels, 100, seed=0)
    assert np.array_equal(sel, np.arange(100))
    with pytest.raises(ValueError):
        rde.stratified_subset(labels, 101, seed=0)


def _synthetic_data(m_epe_tr=40, m_eppe_tr=50, m_eppe_va=12, m_eppe_te=12):
    """构造最小统一数据集 dict：EPE train + EPPE train/val/test。"""
    rng = np.random.default_rng(3)
    m = m_epe_tr + m_eppe_tr + m_eppe_va + m_eppe_te
    x7 = np.zeros((m, 7), dtype=np.float32)
    x7[:, 1] = rng.uniform(140, 550, m)
    x7[:m_epe_tr, 0] = 0.0
    i0 = m_epe_tr
    x7[i0:, 0] = 1.0
    x7[i0:, 1] = rng.choice(CLASSES, m - i0)
    x7[i0:, 2] = rng.uniform(100, 380, m - i0)
    y7 = rng.uniform(0.5, 2.0, (m, 7)).astype(np.float32)
    split = np.zeros(m, dtype=np.int8)
    split[i0 + m_eppe_tr : i0 + m_eppe_tr + m_eppe_va] = 1
    split[i0 + m_eppe_tr + m_eppe_va :] = 2
    grid = np.linspace(0.8, 1.2, 1001)
    return {
        "x7": x7,
        "y7": y7,
        "z_w_db": np.full((m, 1001), 30.0, dtype=np.float32),
        "w_mask": np.ones((m, 1001), dtype=bool),
        "w_grid": grid,
        "f_a": np.ones(m),
        "fp_ratio": np.full(m, 1.02),
        "split": split,
    }


def test_curve_rows_no_leakage_and_roundtrip(tmp_path):
    data = _synthetic_data()
    rows = rde.curve_subset_rows(data, n_eppe=10, seed=20260913)
    tr, va, te = rows[0], rows[1], rows[2]
    src_split = data["split"]
    src_flag = data["x7"][:, 0]
    # 训练子集全部来自 EPPE train split
    assert len(tr) == 10
    assert bool(((src_split[tr] == 0) & (src_flag[tr] == 1)).all())
    # val/test 恰为 EPPE val/test 全集
    assert set(va.tolist()) == set(np.flatnonzero((src_split == 1) & (src_flag == 1)).tolist())
    assert set(te.tolist()) == set(np.flatnonzero((src_split == 2) & (src_flag == 1)).tolist())
    # 无泄漏：train 子集与 test 无交集
    assert not set(tr.tolist()) & set(te.tolist())
    # npz 往返：split 标签与内容保持一致
    out = tmp_path / "sub.npz"
    rde.write_subset_npz(data, rows, out)
    d = np.load(out)
    for key in ("x7", "y7", "z_w_db", "w_mask", "w_grid", "f_a", "fp_ratio", "split"):
        assert key in d.files, key
    assert int((d["split"] == 0).sum()) == 10
    assert int((d["split"] == 2).sum()) == len(te)
    # test 行内容与源完全一致（逐行集合比较）
    src_te = np.sort(data["x7"][te].astype(np.float64), axis=0)
    sub_te = np.sort(d["x7"][d["split"] == 2].astype(np.float64), axis=0)
    assert np.allclose(src_te, sub_te)


def test_pretrain_rows_composition():
    data = _synthetic_data()
    rows = rde.pretrain_rows(data)
    src_split, src_flag = data["split"], data["x7"][:, 0]
    assert bool(((src_split[rows[0]] == 0) & (src_flag[rows[0]] == 0)).all())  # EPE train
    assert bool(((src_split[rows[1]] == 1) & (src_flag[rows[1]] == 0)).all())  # EPE val
    # test = EPE test（空）+ EPPE test
    assert set(rows[2].tolist()) == set(np.flatnonzero(src_split == 2).tolist())


def test_collect_stage1_metrics(tmp_path):
    p = tmp_path / "stage1_eval.json"
    p.write_text(json.dumps({
        "eval": {"eppe": {"fs_mae_mhz": 1.5, "fp_mae_mhz": 2.5, "k2eff_mae_pp": 0.03,
                          "sparse_rmse_db": 0.9, "n_test": 357}},
        "best_epoch": 42,
        "train_seconds": 12.3,
    }))
    m = rde.collect_stage1_metrics(p)
    assert m == {"fs2_mae_mhz": 1.5, "fp2_mae_mhz": 2.5, "k2eff2_mae_pp": 0.03,
                 "sparse_rmse_db": 0.9, "best_epoch": 42, "train_seconds": 12.3,
                 "n_test": 357}


def test_collect_stage2_metrics(tmp_path):
    p = tmp_path / "stage2_eval.json"
    p.write_text(json.dumps({
        "eval": {"eppe": {"full_rmse_db": 1.11, "gt_sparse_full_rmse_db": 0.05,
                          "n_test": 357}},
        "best_epoch": 7,
        "train_seconds": 300.0,
    }))
    m = rde.collect_stage2_metrics(p)
    assert m["stage2_full_rmse_db"] == 1.11
    assert m["stage2_best_epoch"] == 7
    assert m["stage2_train_seconds"] == 300.0


def test_write_xlsx_structure(tmp_path):
    curve = [
        {"n_eppe_train": 10, "mode": "scratch", "seed": 20260913, "fs2_mae_mhz": 9.9,
         "fp2_mae_mhz": 8.8, "k2eff2_mae_pp": 0.5, "sparse_rmse_db": 2.2,
         "best_epoch": 10, "train_seconds": 3.0},
        {"n_eppe_train": 10, "mode": "finetune", "seed": 20260913, "fs2_mae_mhz": 5.5,
         "fp2_mae_mhz": 5.0, "k2eff2_mae_pp": 0.3, "sparse_rmse_db": 1.5,
         "best_epoch": 20, "train_seconds": 4.0},
    ]
    anchor = [
        {"n_eppe_train": 100, "mode": "scratch", "seed": 20260913,
         "fs2_mae_mhz": 4.0, "fp2_mae_mhz": 4.1, "k2eff2_mae_pp": 0.2,
         "sparse_rmse_db": 1.1, "stage2_full_rmse_db": 1.3,
         "stage2_best_epoch": 12, "stage2_train_seconds": 100.0},
    ]
    out = tmp_path / "de.xlsx"
    rde.write_xlsx(curve, anchor, out, readme=rde.readme_lines(seed_base=20260913))
    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames == ["stage1_curve", "pipeline_anchor", "readme"]
    ws = wb["stage1_curve"]
    header = [c.value for c in ws[1]]
    assert header == ["n_eppe_train", "mode", "seed", "fs2_mae_mhz", "fp2_mae_mhz",
                      "k2eff2_mae_pp", "sparse_rmse_db", "best_epoch", "train_seconds"]
    assert ws.max_row == 1 + len(curve)
    ws2 = wb["pipeline_anchor"]
    assert "stage2_full_rmse_db" in [c.value for c in ws2[1]]
    assert ws2.max_row == 1 + len(anchor)
    readme_text = "\n".join(str(r[0].value) for r in wb["readme"].iter_rows())
    assert "357" in readme_text  # EPPE test 口径
    assert "20260913" in readme_text
