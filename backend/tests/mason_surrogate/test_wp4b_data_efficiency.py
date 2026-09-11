"""WP4b 数据效率（run_data_efficiency）的单元测试（TDD 先于实现）。

覆盖：
- stratified_subset：分层比例/最少每类 1 个/确定性/n=全集/超量报错
- curve_subset_rows / write_subset_npz：子集只来自 EPPE train split，无 test 泄漏，
  npz 往返后 split 标签与源数组一致
- finetune_protocols：双协议映射、目录命名向后兼容、非法协议报错
- collect_stage1_metrics / collect_stage2_metrics：从 eval json 提取指标
- write_xlsx：sheet 名、表头（含 protocol 列）、行数、readme 口径说明
- json_safe：NaN/±Inf → None
- subset 训练器冒烟：stage2 的 k 以 stage1 ckpt 为准（cfg 不再含 k）
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


def test_finetune_protocols_default_and_dir_names(tmp_path):
    protos = rde.finetune_protocols(dict(rde.DEFAULT_CFG))
    assert protos == [("finetune", "lowlr", 0.1), ("finetune_full_lr", "full-lr", 1.0)]
    # 目录命名：lowlr 向后兼容既有 s1_finetune_*，full-lr 独立目录
    assert rde.s1_run_dir(tmp_path, "finetune", 10, 1).name == "s1_finetune_n10_s1"
    assert rde.s1_run_dir(tmp_path, "finetune_full_lr", 10, 1).name == (
        "s1_finetune_full_lr_n10_s1")
    only_full = rde.finetune_protocols({**rde.DEFAULT_CFG, "finetune_protocols": "full-lr"})
    assert only_full == [("finetune_full_lr", "full-lr", 1.0)]
    with pytest.raises(ValueError):
        rde.finetune_protocols({**rde.DEFAULT_CFG, "finetune_protocols": "bogus"})


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
        {"n_eppe_train": 10, "mode": "scratch", "protocol": "n/a", "seed": 20260913,
         "fs2_mae_mhz": 9.9, "fp2_mae_mhz": 8.8, "k2eff2_mae_pp": 0.5, "sparse_rmse_db": 2.2,
         "best_epoch": 10, "train_seconds": 3.0},
        {"n_eppe_train": 10, "mode": "finetune", "protocol": "lowlr", "seed": 20260913,
         "fs2_mae_mhz": 5.5, "fp2_mae_mhz": 5.0, "k2eff2_mae_pp": 0.3, "sparse_rmse_db": 1.5,
         "best_epoch": 20, "train_seconds": 4.0},
        {"n_eppe_train": 10, "mode": "finetune", "protocol": "full-lr", "seed": 20260913,
         "fs2_mae_mhz": 4.4, "fp2_mae_mhz": 4.2, "k2eff2_mae_pp": 0.2, "sparse_rmse_db": 1.2,
         "best_epoch": 30, "train_seconds": 5.0},
    ]
    anchor = [
        {"n_eppe_train": 100, "mode": "scratch", "protocol": "n/a", "seed": 20260913,
         "fs2_mae_mhz": 4.0, "fp2_mae_mhz": 4.1, "k2eff2_mae_pp": 0.2,
         "sparse_rmse_db": 1.1, "stage2_full_rmse_db": 1.3,
         "stage2_best_epoch": 12, "stage2_train_seconds": 100.0},
        {"n_eppe_train": 100, "mode": "finetune", "protocol": "full-lr", "seed": 20260913,
         "fs2_mae_mhz": 3.0, "fp2_mae_mhz": 3.1, "k2eff2_mae_pp": 0.1,
         "sparse_rmse_db": 1.0, "stage2_full_rmse_db": 1.1,
         "stage2_best_epoch": 9, "stage2_train_seconds": 90.0},
    ]
    out = tmp_path / "de.xlsx"
    rde.write_xlsx(curve, anchor, out, readme=rde.readme_lines(seed_base=20260913))
    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames == ["stage1_curve", "pipeline_anchor", "readme"]
    ws = wb["stage1_curve"]
    header = [c.value for c in ws[1]]
    assert header == ["n_eppe_train", "mode", "protocol", "seed", "fs2_mae_mhz", "fp2_mae_mhz",
                      "k2eff2_mae_pp", "sparse_rmse_db", "best_epoch", "train_seconds"]
    assert ws.max_row == 1 + len(curve)
    # protocol 列值正确落位
    proto_col = [ws.cell(row=r, column=3).value for r in range(2, ws.max_row + 1)]
    assert set(proto_col) == {"n/a", "lowlr", "full-lr"}
    ws2 = wb["pipeline_anchor"]
    header2 = [c.value for c in ws2[1]]
    assert "protocol" in header2 and "stage2_full_rmse_db" in header2
    assert ws2.max_row == 1 + len(anchor)
    readme_text = "\n".join(str(r[0].value) for r in wb["readme"].iter_rows())
    assert "357" in readme_text  # EPPE test 口径
    assert "20260913" in readme_text
    assert "full-lr" in readme_text and "lowlr" in readme_text  # 双协议口径
    assert "欠训练" in readme_text  # n≥300 反转的方法学说明


def test_json_safe_nan_inf_to_none():
    from mason_surrogate.common import json_safe

    obj = {"a": float("nan"), "b": [1.0, float("inf")], "c": {"d": float("-inf")},
           "e": (float("nan"), 2), "f": "ok", "g": None}
    out = json_safe(obj)
    assert out == {"a": None, "b": [1.0, None], "c": {"d": None}, "e": [None, 2],
                   "f": "ok", "g": None}
    # 转换后可过 allow_nan=False 的标准 JSON 序列化
    json.dumps(out, allow_nan=False)


def test_run_fingerprint_backfill_match_mismatch(tmp_path):
    cfg = dict(rde.DEFAULT_CFG)
    npz = tmp_path / "d.npz"
    np.savez(npz, a=np.zeros(3))
    fp = rde.run_fingerprint(cfg, "stage1", npz, seed=1, lr=1e-3,
                             extra={"n_eppe_train": 10, "mode": "scratch"})
    out = tmp_path / "run1"
    rde.check_fingerprint(out, fp)  # 旧目录无指纹 → 回填
    assert (out / "fingerprint.json").exists()
    rde.check_fingerprint(out, fp)  # 匹配 → 静默通过
    fp_bad = rde.run_fingerprint(cfg, "stage1", npz, seed=1, lr=1e-4,
                                 extra={"n_eppe_train": 10, "mode": "scratch"})
    with pytest.raises(RuntimeError, match="指纹不匹配"):
        rde.check_fingerprint(out, fp_bad)


def test_data_efficiency_conclusions_math():
    rows = []
    for n, v in ((30, 400.0), (100, 100.0), (4052, 20.0)):
        rows.append({"n_eppe_train": n, "mode": "scratch", "protocol": "n/a",
                     "fs2_mae_mhz": v})
    rows.append({"n_eppe_train": 30, "mode": "finetune", "protocol": "lowlr",
                 "fs2_mae_mhz": 200.0})
    rows.append({"n_eppe_train": 4052, "mode": "finetune", "protocol": "full-lr",
                 "fs2_mae_mhz": 16.0})
    lines = rde.data_efficiency_conclusions(rows)
    # lowlr@30=200 → t=ln(400/200)/ln(400/100)=0.5 → n*=30×(100/30)^0.5≈54.8 → ≈55, ≈1.83×
    assert any("n*≈55" in line and "1.83×" in line for line in lines), lines
    # 全量相对改善 (20−16)/20 = +20%
    assert any("+20%" in line for line in lines), lines
    # readme 传入曲线行后追加结论段
    readme = rde.readme_lines(seed_base=20260913, curve_rows=rows)
    assert any("等效 n*" in line for line in readme)


def test_subset_trainers_smoke_k_from_stage1_ckpt(tmp_path):
    """subset stage1(k=32) → subset stage2：k 以 stage1 ckpt 为准，cfg 不再含 k。"""
    import torch
    from mason_surrogate import train_stage1_mason_subset as ts1s
    from mason_surrogate import train_stage2_mason_subset as ts2s

    assert "k" not in ts2s.DEFAULT_CFG, "subset stage2 的 k 应由 stage1 ckpt 提供"

    data = _synthetic_data()
    npz = tmp_path / "tiny.npz"
    rde.write_subset_npz(data, rde.curve_subset_rows(data, n_eppe=10, seed=0), npz)

    s1_cfg = dict(ts1s.DEFAULT_CFG)
    s1_cfg.update(data=str(npz), out_dir=str(tmp_path / "s1"), epochs=2, warmup_epochs=1,
                  batch_size=32, hidden=32, n_res_blocks=1, k=32, patience=5)
    ts1s.train(s1_cfg)

    s2_cfg = dict(ts2s.DEFAULT_CFG)
    s2_cfg.update(data=str(npz), stage1_ckpt=str(tmp_path / "s1" / "stage1.pt"),
                  stage1_config=str(tmp_path / "s1" / "stage1_config.json"),
                  out_dir=str(tmp_path / "s2"), epochs=1, batch_size=32, d_model=16,
                  n_layers=1, n_heads=2, dim_feedforward=32, num_threads=2, patience=5)
    ts2s.train(s2_cfg)
    ckpt = torch.load(tmp_path / "s2" / "stage2.pt", map_location="cpu", weights_only=True)
    assert ckpt["k"] == 32  # 来自 stage1 ckpt，而非任何 cfg 缺省
    s2_conf = json.loads((tmp_path / "s2" / "stage2_config.json").read_text())
    assert s2_conf["k"] == 32 and s2_conf["k_source"].startswith("stage1")
    ev = json.loads((tmp_path / "s2" / "stage2_eval.json").read_text())
    assert ev["eval"]["eppe"]["n_test"] > 0
