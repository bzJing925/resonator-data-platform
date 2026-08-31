"""批次统计服务。

封装物化视图读取 / 实时聚合回退，以及物化视图刷新。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models import Device

logger = logging.getLogger(__name__)


def refresh_mv_batch_stats(db: Session) -> None:
    """刷新 mv_batch_stats 物化视图；失败仅记录日志。"""
    try:
        db.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_batch_stats"))
        db.commit()
    except Exception:
        logger.exception("刷新物化视图 mv_batch_stats 失败（非致命）")


def get_batch_stats(
    db: Session,
    batch_id: int,
    total_dev: int,
) -> dict[str, Any]:
    """获取批次统计：fs 均值 / 中位数 / 合格率。

    优先从 mv_batch_stats 读取；失败时回退到 devices 表实时聚合。
    """
    fs_mean: float | None = None
    fs_median: float | None = None
    pass_rate: float | None = None

    try:
        mv_rows = (
            db.execute(
                text("""
                SELECT
                    COALESCE(SUM(pass_count), 0) AS pass_count,
                    AVG(avg_fs_ghz) AS fs_mean,
                    AVG(median_fs_ghz) AS fs_median
                FROM mv_batch_stats
                WHERE batch_id = :batch_id
            """),
                {"batch_id": batch_id},
            )
            .mappings()
            .all()
        )
        if mv_rows:
            mv = mv_rows[0]
            pass_count = int(mv["pass_count"] or 0)
            fs_mean = mv["fs_mean"]
            fs_median = mv["fs_median"]
            pass_rate = (pass_count / total_dev) if total_dev > 0 else None
    except Exception:
        # 物化视图不存在或查询失败时回退到实时聚合
        fs_mean = db.scalar(select(func.avg(Device.fs_ghz)).where(Device.batch_id == batch_id))
        fs_median = db.scalar(
            select(func.percentile_cont(0.5).within_group(Device.fs_ghz.asc())).where(
                Device.batch_id == batch_id
            )
        )
        pass_count = (
            db.scalar(
                select(func.count())
                .select_from(Device)
                .where(Device.batch_id == batch_id, Device.pf == "Y")
            )
            or 0
        )
        pass_rate = (pass_count / total_dev) if total_dev > 0 else None

    return {
        "fs_ghz_mean": float(fs_mean) if fs_mean is not None else None,
        "fs_ghz_median": float(fs_median) if fs_median is not None else None,
        "pass_rate": pass_rate,
    }
