"""导出实测侧双联图数据 → paper/ius2026/figures/measured_dual_for_origin.xlsx

面板 (a)：parity 散点——208 个留出测试器件、7 项指标的预测值 vs 真值
          （当前生产 checkpoint，含 Zp 校正器；同 paper_metrics_ius.py 的推理路径）。
面板 (b)：训练曲线——Stage 1 分量损失（生产配置 seed 0 重跑，分量日志）
          + Stage 2 Step B（生产运行原始记录）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import AlgorithmConfig  # noqa: E402
from app.ml.surrogate.infer import SurrogatePredictor  # noqa: E402

PARAM_COLS = list(AlgorithmConfig.surr_param_cols)
OUT = Path(
    "/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/paper/ius2026/figures/"
    "measured_dual_for_origin.xlsx"
)
S1_HIST = Path("/tmp/ckpt_s1_logrun/stage1_history.json")
S2_HIST = PROJECT_ROOT / "ckpt_sim600/stage2_ft_history.json"


def r2(t: np.ndarray, p: np.ndarray) -> float:
    ss_res = float(np.sum((t - p) ** 2))
    ss_tot = float(np.sum((t - t.mean()) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")


def main() -> None:
    data_dir = Path("surrogate_data")
    meta = json.loads((data_dir / "meta.json").read_text(encoding="utf-8"))
    with np.load(data_dir / "dataset.npz") as z:
        npz = {k: z[k] for k in z.files}
    test_groups = {i for i, g in enumerate(meta["groups"]) if g["split"] == "test"}
    idx = [i for i, ci in enumerate(npz["group_idx"]) if ci in test_groups]

    pred = SurrogatePredictor(
        "checkpoints_surrogate/stage1.pt",
        "checkpoints_surrogate/stage2_ft.pt",
        "checkpoints_surrogate/stage2_config.json",
        "surrogate_data/baseline.npz",
        "surrogate_data/meta.json",
    )
    yt, yp = {}, {}
    for c in PARAM_COLS:
        yt[c], yp[c] = [], []
    for i in idx:
        eg, fl, _ag, area, pf = npz["x_struct"][i]
        r = pred.predict(float(eg), float(fl), float(area), float(pf))
        for j, c in enumerate(PARAM_COLS):
            yt[c].append(float(npz["y_params"][i][j]))
            yp[c].append(float(r["params"][c]))
    for c in PARAM_COLS:
        yt[c] = np.array(yt[c])
        yp[c] = np.array(yp[c])

    wb = Workbook()
    ws = wb.active
    ws.title = "README"
    for row in [
        ["实测侧双联图数据源（Origin 绘图用）"],
        [],
        ["面板 (a) parity 散点", "sheet parity：208 个留出测试器件 × 7 项指标的真值(true)与"
         "预测值(pred)，每指标一列对。sheet r2 给出每指标的 R²/MAE，供图内标注用。"
         "单位：fs/fp 为 GHz，k2eff_pct 为百分点，Q 无量纲。"],
        ["面板 (b) 训练曲线", "sheet stage1（101 epochs）：L1 = MSE_params + 0.1·MSE_sparse"
         "（z-score 空间），生产配置 seed 0 重跑+分量日志（best val 0.06957，与生产 0.06959"
         "一致）；sheet stage2_stepB（80 epochs）：生产 Step B 原始记录，优化目标为峰加权"
         " MSE（w=1+3·relu(z−1)），val_w 即该加权 MSE（早停准则）。"],
        [],
        ["注", "推理路径与论文数字同源：surrogate_data + checkpoints_surrogate（含 Zp 校正器）。"],
    ]:
        ws.append(row)
    ws["A1"].font = Font(bold=True, size=13)
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 110

    wsr = wb.create_sheet("r2")
    wsr.append(["param", "unit", "R2", "MAE"])
    for c in wsr[1]:
        c.font = Font(bold=True)
    for c in PARAM_COLS:
        unit = "GHz" if c in ("fs_ghz", "fp_ghz") else ("pct" if c == "k2eff_pct" else "1")
        mae = float(np.mean(np.abs(yt[c] - yp[c])))
        wsr.append([c, unit, r2(yt[c], yp[c]), mae])
    for row in wsr.iter_rows(min_row=2):
        for cell in row[2:4]:
            cell.number_format = "0.0000"

    wsp = wb.create_sheet("parity")
    hdr = []
    for c in PARAM_COLS:
        hdr += [f"{c}_true", f"{c}_pred"]
    wsp.append(hdr)
    for c in wsp[1]:
        c.font = Font(bold=True)
    for i in range(len(idx)):
        row = []
        for c in PARAM_COLS:
            row += [float(yt[c][i]), float(yp[c][i])]
        wsp.append(row)

    h1 = json.loads(S1_HIST.read_text(encoding="utf-8"))
    ws1 = wb.create_sheet("stage1")
    ws1.append(["epoch", "train_total", "val_total", "train_params_mse", "val_params_mse",
                "train_sparse_mse", "val_sparse_mse"])
    for c in ws1[1]:
        c.font = Font(bold=True)
    for h in h1:
        ws1.append([h["epoch"], h["train"], h["val"], h["train_params"], h["val_params"],
                    h["train_sparse"], h["val_sparse"]])

    h2 = json.loads(S2_HIST.read_text(encoding="utf-8"))
    ws2 = wb.create_sheet("stage2_stepB")
    ws2.append(["epoch", "train_mse", "val_mse", "val_peak_weighted_mse"])
    for c in ws2[1]:
        c.font = Font(bold=True)
    for h in h2:
        ws2.append([h["epoch"], h["train"], h["val"], h["val_w"]])

    for sheet in (wsr, wsp, ws1, ws2):
        for i, col in enumerate(sheet.columns, 1):
            sheet.column_dimensions[get_column_letter(i)].width = 14
        sheet.freeze_panes = "A2"

    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)
    print(f"saved: {OUT}  (parity: {len(idx)} devices × {len(PARAM_COLS)} params)")


if __name__ == "__main__":
    main()
