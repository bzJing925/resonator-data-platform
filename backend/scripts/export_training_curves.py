"""导出训练曲线数据 → paper/ius2026/figures/training_curves_for_origin.xlsx

Stage 1（生产配置重跑，seed 0，分量日志）：/tmp/ckpt_s1_logrun/stage1_history.json
  损失 L1 = MSE(params) + 0.1·MSE(sparse)，目标均 z-score；
  训练集混入仿真补充行（权重 +0.3，Q 通道掩码），仿真行不进 sparse 损失。
Stage 2 Step B（生产运行记录）：ckpt_sim600/stage2_ft_history.json
  优化目标 = 峰加权 MSE：w = 1 + 3·relu(z_norm − 1)（z_norm 为 z-score 后的谱值）；
  train/val 为普通 MSE，val_w 为峰加权 MSE（早停准则）。
"""

from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

OUT = Path(
    "/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/paper/ius2026/figures/"
    "training_curves_for_origin.xlsx"
)
S1 = Path("/tmp/ckpt_s1_logrun/stage1_history.json")
S2 = Path("/Users/jingbozuo/Projects/aln-data-backend-ml/backend/ckpt_sim600/stage2_ft_history.json")


def main() -> None:
    h1 = json.loads(S1.read_text(encoding="utf-8"))
    h2 = json.loads(S2.read_text(encoding="utf-8"))

    wb = Workbook()
    ws = wb.active
    ws.title = "README"
    for row in [
        ["IUS 论文训练曲线数据源（Origin 绘图用，x 轴 = epoch）"],
        [],
        ["stage1", "Stage 1 基准锚定残差 MLP。生产配置重跑（seed 0，surrogate_data + "
         "sim_supplement 混合，sim 权重 0.3），逐 epoch 总损失与分量已与生产历史"
         "（ckpt_sim600_s0）逐点核对一致。"],
        ["", "L1 = MSE_params + 0.1 * MSE_sparse（z-score 目标空间）；"
         "MSE_params 为 7 项指标（Q 通道对 sim 行掩码），MSE_sparse 为 256 稀疏点。"],
        ["", "列：epoch | train/val_total | train/val_params_mse | train/val_sparse_mse"],
        ["stage2_stepB", "Stage 2 Transformer 重建器 Step B 微调（生产运行原始记录，"
         "ckpt_sim600/stage2_ft_history.json，2026-08-19，early stop 于 epoch 80）。"],
        ["", "优化目标 = 峰加权 MSE：w = 1 + 3*relu(z_norm-1)；val_w 为该加权 MSE"
         "（早停准则）；train/val_mse 为普通 MSE。"],
        ["", "列：epoch | train_mse | val_mse | val_peak_weighted_mse"],
        [],
        ["注", "R² 不属于损失函数（评估指标），逐目标 R² 见 metrics_for_origin.xlsx。"],
    ]:
        ws.append(row)
    ws["A1"].font = Font(bold=True, size=13)

    ws1 = wb.create_sheet("stage1")
    hdr1 = ["epoch", "train_total", "val_total", "train_params_mse", "val_params_mse",
            "train_sparse_mse", "val_sparse_mse"]
    ws1.append(hdr1)
    for c in ws1[1]:
        c.font = Font(bold=True)
    for h in h1:
        ws1.append([h["epoch"], h["train"], h["val"], h["train_params"], h["val_params"],
                    h["train_sparse"], h["val_sparse"]])

    ws2 = wb.create_sheet("stage2_stepB")
    ws2.append(["epoch", "train_mse", "val_mse", "val_peak_weighted_mse"])
    for c in ws2[1]:
        c.font = Font(bold=True)
    for h in h2:
        ws2.append([h["epoch"], h["train"], h["val"], h["val_w"]])

    for sheet in (ws1, ws2):
        for i, col in enumerate(sheet.columns, 1):
            sheet.column_dimensions[get_column_letter(i)].width = 16
        sheet.freeze_panes = "A2"
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 110

    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)
    print(f"saved: {OUT}  (stage1: {len(h1)} epochs, stage2_stepB: {len(h2)} epochs)")


if __name__ == "__main__":
    main()
