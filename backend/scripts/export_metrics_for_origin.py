"""导出 IUS 论文两张 MSE+R² 图的 Origin 数据源 → paper/ius2026/figures/metrics_for_origin.xlsx

Sheet sim_5fold：仿真侧 5 折逐参数指标（源：FBAR_5Fold_逐参数性能指标_均值±标准差.xlsx，
1060 samples / 20 numeric inputs / 5-fold CV，Direct 与 Physics-derived 两路线 × 6 模型）。
MSE 列为 Excel 公式 =RMSE²。
Sheet measured_test：实测侧 29 留出组 208 器件（源：scripts/paper_metrics_ius.py 输出
eval_out_current/measured_metrics.json，当前生产 checkpoint）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

SRC_XLSX = Path(
    "/Users/jingbozuo/Library/Containers/com.tencent.xinWeChat/Data/Documents/"
    "xwechat_files/wxid_c84jmpw4gklf11_1848/msg/file/2026-08/"
    "FBAR_5Fold_逐参数性能指标_均值±标准差.xlsx"
)
MEAS_JSON = Path("eval_out_current/measured_metrics.json")
OUT = Path(
    "/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/paper/ius2026/figures/"
    "metrics_for_origin.xlsx"
)

PARAMS = ["fs", "fp", "Qs", "Qp", "Qmax", "Qmax_freq", "k2", "FOM"]
UNITS = {
    "fs": "MHz",
    "fp": "MHz",
    "Qmax_freq": "MHz",
    "k2": "pct",
    "Qs": "1",
    "Qp": "1",
    "Qmax": "1",
    "FOM": "1",
}


def parse_pm(s: object) -> tuple[float, float]:
    m = re.match(r"\s*([-\d.]+)\s*±\s*([-\d.]+)", str(s))
    return (float(m.group(1)), float(m.group(2))) if m else (float("nan"), float("nan"))


def main() -> None:
    xl = pd.ExcelFile(SRC_XLSX)
    rows = []
    for p in PARAMS:
        df = xl.parse(p, header=None, skiprows=4)
        df.columns = ["route", "model", "mae", "rmse", "r2", "nrmse"]
        for _, r in df.iterrows():
            mae_m, mae_s = parse_pm(r["mae"])
            rmse_m, rmse_s = parse_pm(r["rmse"])
            r2_m, r2_s = parse_pm(r["r2"])
            nrmse_m, nrmse_s = parse_pm(r["nrmse"])
            rows.append(
                [p, UNITS[p], r["route"], r["model"], mae_m, mae_s, rmse_m, rmse_s,
                 None, r2_m, r2_s, nrmse_m, nrmse_s]
            )

    wb = Workbook()
    ws = wb.active
    ws.title = "README"
    readme = [
        ["IUS 论文 MSE+R² 图数据源（Origin 绘图用）"],
        [],
        ["sim_5fold", "仿真模型（COMSOL D1/D2 语料快照：1060 samples, 20 numeric inputs, "
         "5-fold CV, mean±SD）。源文件：FBAR_5Fold_逐参数性能指标_均值±标准差.xlsx (2026-08)。"],
        ["", "MSE 列为公式 =RMSE_mean^2（fs/fp/Qmax_freq 单位 MHz²，k2 为百分点²，其余无量纲²）。"],
        ["measured_test", "实测模型：29 个留出物理组 / 208 器件，当前生产 checkpoint "
         "（checkpoints_surrogate，含 Zp 校正器），surrogate_data（仿真参考清洗版）。"],
        ["", "源文件：backend/eval_out_current/measured_metrics.json "
         "（scripts/paper_metrics_ius.py 生成，2026-09-03）。"],
        ["", "spectrum = 1001 点全谱逐器件 MSE 均值；sparse_256 = 真值 fs/fp 位置上 256 稀疏点。"],
    ]
    for row in readme:
        ws.append(row)
    ws["A1"].font = Font(bold=True, size=13)

    ws1 = wb.create_sheet("sim_5fold")
    hdr = ["param", "unit", "route", "model", "MAE_mean", "MAE_SD", "RMSE_mean", "RMSE_SD",
           "MSE_mean(=RMSE^2)", "R2_mean", "R2_SD", "NRMSE_mean", "NRMSE_SD"]
    ws1.append(hdr)
    for c in ws1[1]:
        c.font = Font(bold=True)
    for row in rows:
        ws1.append(row)
    for i in range(2, len(rows) + 2):
        ws1.cell(row=i, column=9).value = f"=G{i}^2"
        for j in range(5, 14):
            if j != 9:
                ws1.cell(row=i, column=j).number_format = "0.0000"
    ws1.cell(row=1, column=9).number_format = "General"

    meas = json.loads(MEAS_JSON.read_text(encoding="utf-8"))
    ws2 = wb.create_sheet("measured_test")
    ws2.append(["target", "unit", "MSE", "RMSE", "MAE", "R2", "note"])
    for c in ws2[1]:
        c.font = Font(bold=True)
    for col, d in meas["params"].items():
        ws2.append([col, d["unit"], d["mse"], d["rmse"], d["mae"], d["r2"], ""])
    sp = meas["spectrum"]
    ws2.append(["spectrum_1001pt", "dB", sp["mse_db2"], sp["rmse_db"], None,
                sp["r2_macro"], "R2 = 逐器件 R2 均值 (pooled: %.4f)" % sp["r2_pooled"]])
    s256 = meas["sparse_256"]
    ws2.append(["sparse_256pt", "dB", s256["mse_db2"], s256["rmse_db"], None,
                s256["r2_pooled"], "真值 fs/fp 采样位置；R2 为 pooled"])
    for row in ws2.iter_rows(min_row=2):
        for c in row[2:6]:
            c.number_format = "0.0000"

    for sheet in (ws1, ws2):
        for i, col in enumerate(sheet.columns, 1):
            w = max(len(str(c.value)) for c in col if c.value is not None)
            sheet.column_dimensions[get_column_letter(i)].width = min(w + 2, 40)
        sheet.freeze_panes = "A2"
    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 110

    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)
    print(f"saved: {OUT}  sim rows: {len(rows)}")


if __name__ == "__main__":
    main()
