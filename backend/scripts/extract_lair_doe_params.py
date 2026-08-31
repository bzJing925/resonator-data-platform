"""一次性任务：提取「L_air = L_bot-6[um]」DOE 仿真 S1P 全量参数表。

对数据集目录下全部 .s1p 文件跑核心提取流程（extract_resonator_params），
结构参数（面积 / EG / Frame / AG / PF）从 DOE 训练映射 Excel 的
Training_Index 表按 SNP_Relative_Path 关联，输出 CSV + Markdown 两个表格。

用法（在 backend/ 目录下）：
    uv run python scripts/extract_lair_doe_params.py
"""

from __future__ import annotations

import csv
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 可用环境变量指定提供 app 包的 backend 路径（例如在算法 worktree 修复
# 未合并时，用修复后的代码重跑提取）：
#   ALN_EXTRACT_BACKEND=/path/to/worktree/backend uv run python scripts/extract_lair_doe_params.py
BACKEND_ROOT = Path(os.environ.get("ALN_EXTRACT_BACKEND", str(PROJECT_ROOT)))
sys.path.insert(0, str(BACKEND_ROOT))

REPO_ROOT = PROJECT_ROOT.parent
SNP_ROOT = REPO_ROOT / "L_air = L_bot-6[um]"
XLSX_PATH = REPO_ROOT / "D1_D2_DOE_SNP_training_map_Lair_Lbot_minus6um.xlsx"
OUT_CSV = SNP_ROOT / "D1_D2_extracted_params_Lair_Lbot_minus6um.csv"
OUT_MD = SNP_ROOT / "D1_D2_extracted_params_Lair_Lbot_minus6um.md"

# 输出列（表头, 行 dict key）；顺序即用户需求顺序
COLUMNS: list[tuple[str, str]] = [
    ("文件名", "filename"),
    ("面积 (um²)", "area_um2"),
    ("EG类型", "eg_state"),
    ("EG高 (nm)", "h_eg_nm"),
    ("EG宽 (um)", "l_eg_um"),
    ("Frame高 (nm)", "h_frame_nm"),
    ("Frame宽 (um)", "l_frame_um"),
    ("AG宽 (um)", "l_ag_um"),
    ("AG高 (nm)", "h_ag_nm"),
    ("PF (nm)", "pf_nm"),
    ("fs (GHz)", "fs_ghz"),
    ("fp (GHz)", "fp_ghz"),
    ("kt2 (%)", "k2eff_pct"),
    ("Qs", "qs"),
    ("Qp", "qp"),
    ("bodeQ", "bodeq_fitted"),
]


def _extract_one(path_str: str) -> dict:
    """Worker：单文件提取，只保留需要的字段。异常时返回带 error 的 dict。"""
    from app.core.extract import extract_resonator_params

    p = Path(path_str)
    row: dict = {
        "rel_path": p.relative_to(SNP_ROOT).as_posix(),
        "filename": p.name,
    }
    try:
        r = extract_resonator_params(p, skip_validation=True)
        row.update(
            fs_ghz=round(r["fs_ghz"], 6),
            fp_ghz=round(r["fp_ghz"], 6),
            k2eff_pct=round(r["k2eff_pct"], 4),
            qs=round(r["qs"], 2),
            qp=round(r["qp"], 2),
            bodeq_fitted=round(r["bodeq_fitted"], 2),
        )
    except Exception as exc:  # noqa: BLE001 — 批量任务，单个失败不致命
        row["error"] = f"{type(exc).__name__}: {exc}"
    return row


def _load_geometry_map() -> dict[str, dict]:
    """Training_Index: 规范化相对路径 → 结构参数 + Training_Row_ID。"""
    import openpyxl

    wb = openpyxl.load_workbook(XLSX_PATH, read_only=True, data_only=True)
    ws = wb["Training_Index"]
    rows = list(ws.iter_rows(values_only=True))
    idx = {h: i for i, h in enumerate(rows[0])}
    geo: dict[str, dict] = {}
    for r in rows[1:]:
        rel = r[idx["SNP_Relative_Path"]]
        if not rel:
            continue
        key = rel.replace("\\", "/")
        geo[key] = {
            "row_id": r[idx["Training_Row_ID"]],
            "area_um2": r[idx["SNP_A_res_um2"]],
            "eg_state": r[idx["EG_State"]],
            "h_eg_nm": r[idx["SNP_h_EG_nm"]],
            "l_eg_um": r[idx["SNP_L_EG_um"]],
            "h_frame_nm": r[idx["SNP_h_FL_nm"]],
            "l_frame_um": r[idx["SNP_L_FL_um"]],
            "h_ag_nm": r[idx["SNP_h_AG_nm"]],
            "l_ag_um": r[idx["SNP_L_AG_um"]],
            "pf_nm": r[idx["SNP_PF_nm"]],
            "has_pf": r[idx["has_PF"]],
        }
    return geo


def main() -> None:
    import os

    files = sorted(SNP_ROOT.rglob("*.s1p"))
    print(f"共 {len(files)} 个 .s1p 文件", flush=True)

    workers = min(10, (os.cpu_count() or 4))
    rows: list[dict] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_extract_one, str(p)): p for p in files}
        for i, fut in enumerate(as_completed(futs), 1):
            rows.append(fut.result())
            if i % 200 == 0:
                print(f"  进度 {i}/{len(files)}", flush=True)

    geo = _load_geometry_map()
    n_unmapped = 0
    for row in rows:
        g = geo.get(row["rel_path"])
        if g is None:
            n_unmapped += 1
            row["row_id"] = "ZZZ-unmapped"
            print(f"  [警告] Excel 中无映射: {row['rel_path']}", flush=True)
        else:
            row.update(g)
        # 无 PF 层的行 Excel 记 None，统一显示为 0；has_PF=1 但厚度未记录的保持空白
        if row.get("pf_nm") is None and row.get("has_pf") == 0:
            row["pf_nm"] = 0
    errors = [r for r in rows if "error" in r]

    # 按 Training_Row_ID 排序（TR-0001 ...），未映射的排最后
    rows.sort(key=lambda r: (r.get("row_id", "ZZZ"), r["rel_path"]))

    def _fmt(v: object) -> str:
        return "" if v is None else str(v)

    headers = [h for h, _ in COLUMNS]
    with OUT_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for r in rows:
            w.writerow([_fmt(r.get(k)) for _, k in COLUMNS])

    with OUT_MD.open("w", encoding="utf-8") as f:
        f.write("# D1/D2 DOE 仿真 S1P 参数提取表（L_air = L_bot-6[um]）\n\n")
        f.write(
            f"共 {len(rows)} 个文件；提取脚本 `backend/scripts/extract_lair_doe_params.py`，"
            "核心算法 `backend/app/core/extract.py`（bodeQ = 洛伦兹拟合峰值 bodeq_fitted，"
            "kt2 = (π²/4)·(fs/fp)·((fp−fs)/fp)×100%）。\n"
            "PF 列为空白的 12 行（D2-C3）在 Excel 映射中 has_PF=1 但厚度未记录。\n\n"
        )
        f.write("| " + " | ".join(headers) + " |\n")
        f.write("|" + "|".join(["---"] * len(headers)) + "|\n")
        for r in rows:
            f.write("| " + " | ".join(_fmt(r.get(k)) for _, k in COLUMNS) + " |\n")

    print(f"\n完成：{len(rows)} 行（提取失败 {len(errors)}，Excel 无映射 {n_unmapped}）")
    for r in errors[:20]:
        print(f"  [失败] {r['rel_path']}: {r['error']}")
    print(f"CSV: {OUT_CSV}")
    print(f"MD : {OUT_MD}")


if __name__ == "__main__":
    main()
