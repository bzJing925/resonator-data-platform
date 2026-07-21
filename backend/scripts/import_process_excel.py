"""一次性导入工艺参数 Excel 到 process_*_runs 三表。

用法（从 backend 目录）：
    uv run python scripts/import_process_excel.py <xlsx路径> [--replace]

目标表非空时默认报错退出；--replace 先清空三表再导入。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让 `python scripts/xxx.py` 可直接运行（sys.path[0] 默认是 scripts/）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, select

from app.core.process_params import parse_process_workbook
from app.db import SessionLocal
from app.models import ProcessAlnDepRun, ProcessIcpEtchRun, ProcessMoDepRun

_TABLES = {
    "icp_etch": ProcessIcpEtchRun,
    "mo_dep": ProcessMoDepRun,
    "aln_dep": ProcessAlnDepRun,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="导入工艺参数 Excel")
    parser.add_argument("xlsx", help="工艺参数 xlsx 文件路径")
    parser.add_argument("--replace", action="store_true", help="先清空三张工艺表再导入")
    args = parser.parse_args()

    try:
        data = parse_process_workbook(args.xlsx)
    except (ValueError, FileNotFoundError) as exc:
        print(f"解析失败: {exc}", file=sys.stderr)
        return 1

    db = SessionLocal()
    try:
        counts = {
            key: db.scalar(select(func.count()).select_from(model)) or 0
            for key, model in _TABLES.items()
        }
        if any(counts.values()) and not args.replace:
            print(f"错误：工艺表非空 {counts}，如需重新导入请加 --replace", file=sys.stderr)
            return 1
        if args.replace:
            for model in _TABLES.values():
                db.execute(delete(model))

        for key, model in _TABLES.items():
            db.add_all(model(**row) for row in getattr(data, key))
        db.commit()
    finally:
        db.close()

    print("导入完成：")
    for sheet, rep in data.report.sheets.items():
        print(f"  {sheet}: 导入 {rep.imported} 行, 跳过 {rep.skipped} 行")
    if data.report.dirty_cells:
        print(f"清洗脏单元格 {len(data.report.dirty_cells)} 个（置 NULL）：")
        for d in data.report.dirty_cells:
            print(f"  {d.sheet}!{d.cell} = {d.raw!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
