"""上传 ZIP → 解压 → 整理待计算文件 的 Celery 任务。

该任务不再自动拆分 .s2p；而是把原始文件（含 .s1p / .s2p）整理成带端口信息的
待计算项列表，真正的指标计算由 aln.compute_batch 负责。
"""

from __future__ import annotations

import logging
import os
import shutil
import zipfile
import zipfile_deflate64  # noqa: F401  注册 Deflate64 压缩支持
from pathlib import Path
from typing import Any, Callable

from celery import Task

from app.config import get_settings
from app.core.filename import parse_filename
from app.core.touchstone import detect_snp_type
from app.db import SessionLocal
from app.models import Batch, Mapping
from app.workers import celery_app
from app.workers.progress import ProgressPublisher

logger = logging.getLogger(__name__)


def _find_7z() -> str | None:
    """查找 7z / 7za / p7zip 可执行文件。"""
    for name in ("7z", "7za", "p7zip"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _extract_with_7z(zip_path: Path, target_dir: Path, exe: str) -> None:
    """使用 7z/p7zip 解压；支持 Deflate64 与 ZIP64 大文件。"""
    import subprocess

    target_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [exe, "x", "-y", "-o" + str(target_dir), str(zip_path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _extract_with_unzip(zip_path: Path, target_dir: Path) -> None:
    """使用系统 unzip 解压（无法处理 Deflate64 / >4GB 中央目录）。"""
    import subprocess

    target_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["unzip", "-q", "-o", str(zip_path), "-d", str(target_dir)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _extract_zip(
    zip_path: Path,
    target_dir: Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> None:
    """解压 ZIP；支持进度回调 (current, total)。

    策略：
    1. 若系统安装了 7z/p7zip，优先使用它（支持 Deflate64 / ZIP64 大文件，速度通常更快）；
    2. 否则使用 Python zipfile（自带 Deflate64 补丁），可提供逐文件进度；
    3. Python 不支持时回退到系统 unzip。
    """
    exe7z = _find_7z()
    if exe7z:
        if progress_callback:
            progress_callback(0, 1)
        logger.info("使用 7z 解压: %s", exe7z)
        _extract_with_7z(zip_path, target_dir, exe7z)
        if progress_callback:
            progress_callback(1, 1)
        return

    try:
        with zipfile.ZipFile(str(zip_path)) as zf:
            members = [m for m in zf.infolist() if not m.is_dir()]
            total = len(members)
            for i, member in enumerate(members, start=1):
                zf.extract(member, target_dir)
                if progress_callback and total > 0:
                    progress_callback(i, total)
        return
    except (NotImplementedError, RuntimeError) as exc:
        msg = str(exc).lower()
        if "compression method" not in msg and "not supported" not in msg:
            raise
        logger.warning("Python zipfile 不支持该压缩方法，尝试 unzip: %s", exc)

    if progress_callback:
        progress_callback(0, 1)
    if shutil.which("unzip"):
        logger.info("使用 unzip 解压")
        _extract_with_unzip(zip_path, target_dir)
    else:
        raise RuntimeError("ZIP 压缩方法不受支持，且系统未安装 7z / unzip")
    if progress_callback:
        progress_callback(1, 1)


@celery_app.task(bind=True, name="aln.extract_batch")
def extract_batch_task(
    self: Task,
    upload_task_id: int,
    zip_path: str,
    batch_no: str,
    mapping_id: int,
    f_start_ghz: float | None = None,
    f_end_ghz: float | None = None,
    deembed_enabled: bool = False,
    deembed_method: str = "default",
    process_type: str = "AUTO",
) -> dict[str, Any]:
    """解压并整理出所有待计算文件项。

    返回可被 aln.compute_batch 消费的 dict：
    {
        "upload_task_id": int,
        "batch_id": int,
        "mapping_id": int,
        "wafer": int | None,
        "f_start_ghz": ...,
        "f_end_ghz": ...,
        "all_files": [
            {"path": str, "deembedded": bool, "port": int, "s_param_relpath": str},
            ...
        ],
    }
    """
    publisher = ProgressPublisher(upload_task_id)
    settings = get_settings()
    db = SessionLocal()

    def _update_extract_pct(current: int, total: int) -> None:
        pct = int(40 * current / total) if total else 0
        publisher.stage_update(
            db,
            stage="extract",
            stage_progress_pct=pct,
            progress_pct=pct,
            progress_msg=f"解压中… {current}/{total}",
        )

    try:
        publisher.start(db, msg="开始解压…")

        from sqlalchemy import select

        batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
        if batch is None:
            raise RuntimeError(f"batches 表无 batch_no={batch_no} 的预占行")

        mapping_row = db.get(Mapping, mapping_id)
        if mapping_row is None:
            raise RuntimeError(f"mappings 表无 id={mapping_id}")

        # 1. 解压
        target_dir = settings.files_dir / batch_no
        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        _extract_zip(Path(zip_path), target_dir, progress_callback=_update_extract_pct)

        # 2. .snp 通用扩展名自动识别并重命名
        for snp_file in target_dir.rglob("*.snp"):
            try:
                detected = detect_snp_type(snp_file)
                new_ext = ".s1p" if detected == "S1P" else ".s2p"
                snp_file.rename(snp_file.with_suffix(new_ext))
            except Exception as exc:
                logger.warning("识别 .snp 失败 %s: %s", snp_file.name, exc)

        # 3. 扫描（区分 DUT 与校准件；校准件仅用于手动拆分/去嵌，不再自动拆分）
        s2p_files = sorted(p for p in target_dir.rglob("*.s2p") if p.is_file())
        s1p_files = sorted(p for p in target_dir.rglob("*.s1p") if p.is_file())

        dut_s2p: list[Path] = []
        for p in s2p_files:
            parsed = parse_filename(p.name)
            if parsed.is_calibration:
                continue
            dut_s2p.append(p)

        standalone_s1p: list[Path] = []
        for p in s1p_files:
            parsed = parse_filename(p.name)
            if parsed.is_calibration:
                continue
            standalone_s1p.append(p)

        if deembed_enabled and dut_s2p:
            raise RuntimeError(
                "已启用 De-embedding 的批次暂不支持直接处理 .s2p DUT，"
                "请先使用手动拆分工具将 .s2p 拆为 .s1p，或关闭 De-embed 选项。"
            )

        publisher.stage_update(
            db,
            stage="extract",
            stage_progress_pct=60,
            progress_pct=15,
            progress_msg=f"解压完成，发现 {len(dut_s2p)} 个 .s2p DUT、{len(standalone_s1p)} 个 .s1p DUT",
        )

        # 4. 生成待计算文件项：s2p 分 S11/S22 两个端口；s1p 一个端口
        all_files: list[dict[str, Any]] = []
        for s2p in dut_s2p:
            relpath = str(s2p.relative_to(target_dir))
            for port in (0, 1):
                all_files.append(
                    {
                        "path": str(s2p),
                        "deembedded": False,
                        "port": port,
                        "s_param_relpath": relpath,
                    }
                )
        for s1p in standalone_s1p:
            all_files.append(
                {
                    "path": str(s1p),
                    "deembedded": False,
                    "port": 0,
                    "s_param_relpath": str(s1p.relative_to(target_dir)),
                }
            )

        if not all_files:
            raise RuntimeError("ZIP 解压后未发现可处理的 DUT 文件（.s1p 或 .s2p）")

        wafer = _wafer_from_batch_no(batch_no)

        # 记录解压目录，供 compute_batch 使用
        batch.file_path = str(target_dir)
        db.commit()

        # 按配置清理原 zip，避免 raw zip + 解压文件双重占盘
        if not settings.KEEP_RAW_ZIP:
            try:
                raw_zip = Path(zip_path)
                if raw_zip.exists():
                    raw_zip.unlink()
                    batch.raw_zip_path = None
                    db.commit()
            except Exception:
                logger.exception("删除原 zip 失败: %s", zip_path)

        publisher.stage_update(
            db,
            stage="extract",
            stage_progress_pct=100,
            progress_pct=30,
            progress_msg=f"文件整理完成，共 {len(all_files)} 个待计算项",
        )

        return {
            "upload_task_id": upload_task_id,
            "batch_id": batch.id,
            "mapping_id": mapping_id,
            "wafer": wafer,
            "f_start_ghz": f_start_ghz,
            "f_end_ghz": f_end_ghz,
            "all_files": all_files,
        }

    except Exception as exc:
        logger.exception("extract_batch_task fatal")
        try:
            db.rollback()
        except Exception:
            pass
        try:
            publisher.fail(db, error_msg=str(exc))
        except Exception:
            logger.exception("publisher.fail itself raised")
        raise
    finally:
        db.close()


def _wafer_from_batch_no(batch_no: str) -> int | None:
    import re

    m = re.search(r"\.(\d+)$", batch_no)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None
