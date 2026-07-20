"""生成 NN 训练 demo CSV。

从桌面版 SQLite 数据库的 #2 批次随机挑选 1000 个器件，导出为宽表 CSV，
供后续神经网络训练验证使用。新功能 UI 未启用，此脚本仅用于产出 demo 数据。

用法（在 backend/ 目录下）:
    $env:ALN_DESKTOP_MODE='true'   # PowerShell
    uv run python scripts/generate_nn_demo.py

输出：项目根目录下的 nn_demo_1000.csv
"""

from __future__ import annotations

import csv
import io
import logging
import os
import sys
from pathlib import Path

from sqlalchemy import func, select

# 必须在导入 app 之前设置桌面模式并修正 sys.path
os.environ.setdefault("ALN_DESKTOP_MODE", "true")

# 项目根目录：backend/../
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

# ruff: app 导入依赖上面的 sys.path 插入，必须放在这里
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Batch, Device  # noqa: E402
from app.services.nn_export import (  # noqa: E402
    _SCALAR_COLUMNS,
    _build_header,
    _build_nn_export_row,
)

log = logging.getLogger("aln")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

BATCH_NO = "#2"
SAMPLE_SIZE = 1000
TARGET_K = 300
TARGET_N_FREQ = 1001
OUTPUT_NAME = "nn_demo_1000.csv"


def main() -> int:
    settings = get_settings()
    if not settings.is_desktop:
        log.error("请设置 ALN_DESKTOP_MODE=true 以使用桌面版 SQLite 数据库")
        return 1

    db = SessionLocal()
    try:
        batch = db.execute(select(Batch).where(Batch.batch_no == BATCH_NO)).scalar_one_or_none()
        if batch is None:
            log.error("未找到批次 %s", BATCH_NO)
            return 1
        log.info(
            "批次 %s 共有 %d 个器件，随机抽取 %d 个",
            BATCH_NO, batch.device_count, SAMPLE_SIZE,
        )

        # 标量列 + batch_no
        select_cols = [getattr(Device, c).label(c) for c in _SCALAR_COLUMNS]
        select_cols.append(Batch.batch_no.label("batch_no"))

        # SQLite 用 func.random() 随机排序；PostgreSQL 用 func.random() 同样适用
        stmt = (
            select(*select_cols)
            .select_from(Device)
            .join(Batch, Device.batch_id == Batch.id)
            .where(Batch.batch_no == BATCH_NO)
            .order_by(func.random())
            .limit(SAMPLE_SIZE)
        )

        rows = db.execute(stmt).mappings().all()
        log.info("抽到 %d 个器件", len(rows))

        header = _build_header(TARGET_K, TARGET_N_FREQ)

        out_path = PROJECT_ROOT / OUTPUT_NAME
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=header)
        writer.writeheader()

        written = 0
        skipped = 0
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            f.write(buf.getvalue())
            buf.seek(0)
            buf.truncate()

            for row in rows:
                out_row = _build_nn_export_row(
                    dict(row), target_k=TARGET_K, target_n_freq=TARGET_N_FREQ,
                )
                if out_row is None:
                    skipped += 1
                    continue
                writer.writerow(out_row)
                f.write(buf.getvalue())
                buf.seek(0)
                buf.truncate()
                written += 1
                if written % 100 == 0:
                    log.info("已写入 %d / %d", written, len(rows))

        log.info("完成：%s （写入 %d 行，跳过 %d 行）", out_path, written, skipped)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
