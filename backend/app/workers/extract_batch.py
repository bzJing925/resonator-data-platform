"""上传 ZIP → 解压 → 整理待计算文件 的 Celery 任务。

该任务不再自动拆分 .s2p；而是把原始文件（含 .s1p / .s2p）整理成带端口信息的
待计算项列表，真正的指标计算由 aln.compute_batch 负责。
"""

from __future__ import annotations

import logging
import queue
import re
import shutil
import subprocess
import threading
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import zipfile_deflate64  # noqa: F401  注册 Deflate64 压缩支持
from celery import Task

from app.config import get_settings
from app.core.deembed import DeembedError, DeembedMethod, _run_deembed
from app.core.filename import parse_filename
from app.core.touchstone import detect_snp_type, split_s2p_to_s1p
from app.db import SessionLocal
from app.models import Batch, Mapping
from app.services.file_tree_service import build_file_tree_from_disk
from app.workers.cancel import TaskCancelledError, is_task_cancelled, raise_if_cancelled
from app.workers.celery_app import celery_app
from app.workers.local_queue import get_local_queue
from app.workers.progress import ProgressPublisher

logger = logging.getLogger(__name__)


def _find_7z() -> str | None:
    """查找 7z / 7za / p7zip 可执行文件。"""
    for name in ("7z", "7za", "p7zip"):
        path = shutil.which(name)
        if path:
            return path
    return None


_PROGRESS_RE = re.compile(r"\(\s*(\d+)%\)")


def _monitor_extracted_size(
    target_dir: Path,
    total: int,
    state: dict[str, Any],
    stop: threading.Event,
    interval: float = 0.5,
) -> None:
    """后台线程：根据已写入目标目录的字节数估算百分比。"""
    while not stop.wait(interval):
        current = sum(p.stat().st_size for p in target_dir.rglob("*") if p.is_file())
        pct = min(99, int(100 * current / total)) if total > 0 else 0
        state["pct"] = pct


def _read_stream_into_queue(stream, q: queue.Queue[str]) -> None:
    for line in stream:
        q.put(line)


def _extract_with_7z(
    zip_path: Path,
    target_dir: Path,
    exe: str,
    progress_callback: Callable[[int, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> float:
    target_dir.mkdir(parents=True, exist_ok=True)
    total = _zip_uncompressed_size(zip_path)
    state: dict[str, Any] = {"pct": 0}
    stop = threading.Event()
    monitor = threading.Thread(
        target=_monitor_extracted_size,
        args=(target_dir, total, state, stop),
        daemon=True,
    )
    monitor.start()

    start = time.perf_counter()
    stderr_lines: list[str] = []
    stdout_q: queue.Queue[str] = queue.Queue()

    proc = subprocess.Popen(
        [exe, "x", "-y", "-bsp1", "-bb0", "-o" + str(target_dir), str(zip_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    def _read_stderr() -> None:
        for line in proc.stderr or []:
            stderr_lines.append(line)

    stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
    stderr_thread.start()
    stdout_thread = threading.Thread(
        target=_read_stream_into_queue, args=(proc.stdout, stdout_q), daemon=True
    )
    stdout_thread.start()

    try:
        while proc.poll() is None:
            if cancel_check and cancel_check():
                proc.terminate()
                try:
                    proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                raise TaskCancelledError("解压已取消")
            try:
                while True:
                    line = stdout_q.get_nowait()
                    m = _PROGRESS_RE.search(line)
                    if m:
                        state["pct"] = int(m.group(1))
            except queue.Empty:
                pass
            if progress_callback:
                progress_callback(state["pct"], 100)
            time.sleep(0.5)
        proc.wait()
    finally:
        stop.set()
        monitor.join(timeout=2.0)
        stdout_thread.join(timeout=2.0)
        stderr_thread.join(timeout=2.0)

    if proc.returncode != 0:
        err = "".join(stderr_lines)[-500:]
        raise RuntimeError(f"7z 解压失败 (exit {proc.returncode}): {err}")

    if progress_callback:
        progress_callback(100, 100)
    return time.perf_counter() - start


def _extract_with_unzip(
    zip_path: Path,
    target_dir: Path,
    progress_callback: Callable[[int, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> float:
    target_dir.mkdir(parents=True, exist_ok=True)
    total = _zip_uncompressed_size(zip_path)
    state = {"pct": 0}
    stop = threading.Event()
    monitor = threading.Thread(
        target=_monitor_extracted_size,
        args=(target_dir, total, state, stop),
        daemon=True,
    )
    monitor.start()

    start = time.perf_counter()
    proc = subprocess.Popen(
        ["unzip", "-q", "-o", str(zip_path), "-d", str(target_dir)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    stderr_lines: list[str] = []

    def _read_stderr() -> None:
        for line in proc.stderr or []:
            stderr_lines.append(line)

    stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
    stderr_thread.start()

    try:
        while proc.poll() is None:
            if cancel_check and cancel_check():
                proc.terminate()
                try:
                    proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                raise TaskCancelledError("解压已取消")
            if progress_callback:
                progress_callback(state["pct"], 100)
            time.sleep(0.5)
        proc.wait()
    finally:
        stop.set()
        monitor.join(timeout=2.0)
        stderr_thread.join(timeout=2.0)

    if proc.returncode != 0:
        err = "".join(stderr_lines)[-500:]
        raise RuntimeError(f"unzip 解压失败 (exit {proc.returncode}): {err}")

    if progress_callback:
        progress_callback(100, 100)
    return time.perf_counter() - start


def _extract_zip(
    zip_path: Path,
    target_dir: Path,
    progress_callback: Callable[[int, int], None] | None = None,
    upload_task_id: int | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[str, float, int, int]:
    """解压 ZIP；返回 (extractor_name, elapsed_seconds, files_count, total_bytes).

    策略：
    1. 若系统安装了 7z/p7zip，优先使用它（支持 Deflate64 / ZIP64 大文件，速度通常更快）；
    2. 否则使用 Python zipfile（自带 Deflate64 补丁），可提供逐文件进度；
    3. Python 不支持时回退到系统 unzip。
    """
    if cancel_check is None and upload_task_id is not None:

        def _cancel_check() -> bool:
            if get_settings().is_desktop:
                return get_local_queue().is_cancelled(upload_task_id)
            return is_task_cancelled(upload_task_id)

        cancel_check = _cancel_check

    exe7z = _find_7z()
    if exe7z:
        if progress_callback:
            progress_callback(0, 100)
        elapsed = _extract_with_7z(
            zip_path,
            target_dir,
            exe7z,
            progress_callback,
            cancel_check=cancel_check,
        )
        extracted = _count_extracted(target_dir)
        return ("7z", elapsed, extracted, _zip_uncompressed_size(zip_path))

    total = 0
    try:
        with zipfile.ZipFile(str(zip_path)) as zf:
            members = [m for m in zf.infolist() if not m.is_dir()]
            total = len(members)
            start = time.perf_counter()
            for i, member in enumerate(members, start=1):
                if upload_task_id is not None:
                    raise_if_cancelled(upload_task_id)
                zf.extract(member, target_dir)
                if progress_callback and total > 0:
                    progress_callback(i, total)
            elapsed = time.perf_counter() - start
        extracted = _count_extracted(target_dir)
        return ("zipfile", elapsed, extracted, _zip_uncompressed_size(zip_path))
    except (NotImplementedError, RuntimeError) as exc:
        msg = str(exc).lower()
        if "compression method" not in msg and "not supported" not in msg:
            raise
        logger.warning("Python zipfile 不支持该压缩方法，尝试 unzip: %s", exc)

    if progress_callback:
        progress_callback(0, 100)
    if shutil.which("unzip"):
        elapsed = _extract_with_unzip(
            zip_path,
            target_dir,
            progress_callback,
            cancel_check=cancel_check,
        )
        extracted = _count_extracted(target_dir)
    else:
        raise RuntimeError("ZIP 压缩方法不受支持，且系统未安装 7z / unzip")
    if progress_callback:
        progress_callback(100, 100)
    return ("unzip", elapsed, extracted, _zip_uncompressed_size(zip_path))


def _count_extracted(target_dir: Path) -> int:
    """统计已解压的文件数（含子目录）。"""
    return sum(1 for p in target_dir.rglob("*") if p.is_file())


def _zip_uncompressed_size(zip_path: Path) -> int:
    """估算 ZIP 内非目录条目总未压缩字节数。"""
    try:
        with zipfile.ZipFile(str(zip_path)) as zf:
            return sum(m.file_size for m in zf.infolist() if not m.is_dir())
    except Exception:
        return 0


def _run_deembed_for_batch(
    dut_s2p: list[Path],
    cal_open: dict[str, Path],
    cal_short: dict[str, Path],
    target_dir: Path,
    method: str,
    progress_callback: Callable[[int, int], None] | None = None,
    upload_task_id: int | None = None,
) -> list[dict[str, Any]]:
    """对 DUT .s2p 批量拆分并做 ShortOpen 去嵌，返回 all_files 条目。"""
    if not dut_s2p:
        return []

    if not cal_open or not cal_short:
        raise DeembedError(
            "已启用 De-embedding 但 ZIP 内未找到 OPEN/SHORT 校准 .s2p 文件；"
            "请确认压缩包包含同名 OPEN/SHORT 文件，或在上传时取消 De-embed 选项。"
        )

    raw_s11_dir = target_dir / "S11_raw"
    raw_s22_dir = target_dir / "S22_raw"
    raw_s11_dir.mkdir(parents=True, exist_ok=True)
    raw_s22_dir.mkdir(parents=True, exist_ok=True)
    s1p_pairs: list[tuple[Path, Path]] = []
    for s2p in dut_s2p:
        split = split_s2p_to_s1p(s2p, out_dir_s11=raw_s11_dir, out_dir_s22=raw_s22_dir)
        s1p_pairs.append((split.s11_path, split.s22_path))

    de_method = DeembedMethod(method) if method else DeembedMethod.DEFAULT
    de_pairs = _run_deembed(
        s1p_pairs,
        cal_open,
        cal_short,
        target_dir,
        method=de_method,
        progress_callback=progress_callback,
    )

    all_files: list[dict[str, Any]] = []
    for s11_de, s22_de in de_pairs:
        all_files.append(
            {
                "path": str(s11_de),
                "deembedded": True,
                "port": 0,
                "s_param_relpath": str(s11_de.relative_to(target_dir)),
            }
        )
        all_files.append(
            {
                "path": str(s22_de),
                "deembedded": True,
                "port": 1,
                "s_param_relpath": str(s22_de.relative_to(target_dir)),
            }
        )
        if upload_task_id is not None:
            raise_if_cancelled(upload_task_id)
    return all_files


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
    raise_if_cancelled(upload_task_id)
    publisher = ProgressPublisher(upload_task_id)
    settings = get_settings()
    db = SessionLocal()

    def _update_extract_pct(current: int, total: int) -> None:
        stage_pct = int(100 * current / total) if total else 0
        overall = int(30 * current / total) if total else 0
        publisher.stage_update(
            db,
            stage="extract",
            stage_progress_pct=stage_pct,
            progress_pct=overall,
            progress_msg=f"解压中… {stage_pct}%",
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

        # 1. 解压并记录耗时
        target_dir = settings.files_dir / batch_no
        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        extractor, elapsed, extracted_count, raw_bytes = _extract_zip(
            Path(zip_path),
            target_dir,
            progress_callback=_update_extract_pct,
            upload_task_id=upload_task_id,
        )
        mb = raw_bytes / 1024 / 1024
        speed = mb / elapsed if elapsed > 0 else 0.0
        logger.info(
            "解压完成: extractor=%s files=%d raw=%.1f MB elapsed=%.2fs speed=%.1f MB/s",
            extractor,
            extracted_count,
            mb,
            elapsed,
            speed,
        )

        # 2. .snp 通用扩展名自动识别并重命名
        for snp_file in target_dir.rglob("*.snp"):
            try:
                detected = detect_snp_type(snp_file)
                new_ext = ".s1p" if detected == "S1P" else ".s2p"
                snp_file.rename(snp_file.with_suffix(new_ext))
            except Exception as exc:
                logger.warning("识别 .snp 失败 %s: %s", snp_file.name, exc)

        # 3. 扫描（区分 DUT 与校准件）
        s2p_files = sorted(p for p in target_dir.rglob("*.s2p") if p.is_file())
        s1p_files = sorted(p for p in target_dir.rglob("*.s1p") if p.is_file())

        dut_s2p: list[Path] = []
        cal_open: dict[str, Path] = {}
        cal_short: dict[str, Path] = {}
        for p in s2p_files:
            parsed = parse_filename(p.name)
            if parsed.is_open:
                cal_open[p.name] = p
            elif parsed.is_short:
                cal_short[p.name] = p
            else:
                dut_s2p.append(p)

        standalone_s1p: list[Path] = []
        for p in s1p_files:
            parsed = parse_filename(p.name)
            if parsed.is_calibration:
                continue
            standalone_s1p.append(p)

        publisher.stage_update(
            db,
            stage="extract",
            stage_progress_pct=100,
            progress_pct=30,
            progress_msg=(
                f"解压完成，{extractor} 解压 {extracted_count} 个文件，"
                f"{mb:.1f} MB / {elapsed:.2f}s ({speed:.1f} MB/s)；"
                f"发现 {len(dut_s2p)} 个 .s2p DUT、{len(standalone_s1p)} 个 .s1p DUT"
            ),
        )

        # 4. 生成待计算文件项
        all_files: list[dict[str, Any]] = []

        if deembed_enabled and dut_s2p:
            publisher.stage_update(
                db,
                stage="deembed",
                stage_progress_pct=0,
                progress_pct=30,
                progress_msg="开始去嵌…",
            )

            def _update_deembed_pct(current: int, total: int) -> None:
                stage_pct = int(100 * current / total) if total else 0
                overall = 30 + int(15 * current / total) if total else 30
                publisher.stage_update(
                    db,
                    stage="deembed",
                    stage_progress_pct=stage_pct,
                    progress_pct=overall,
                    progress_msg=f"去嵌中… {current}/{total} 对",
                )

            all_files.extend(
                _run_deembed_for_batch(
                    dut_s2p,
                    cal_open,
                    cal_short,
                    target_dir,
                    method=deembed_method,
                    progress_callback=_update_deembed_pct,
                    upload_task_id=upload_task_id,
                )
            )
        else:
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
                    "deembedded": bool(deembed_enabled),
                    "port": 0,
                    "s_param_relpath": str(s1p.relative_to(target_dir)),
                }
            )

        if not all_files:
            raise RuntimeError("ZIP 解压后未发现可处理的 DUT 文件（.s1p 或 .s2p）")

        publisher.stage_update(
            db,
            stage="deembed",
            stage_progress_pct=100,
            progress_pct=45,
            progress_msg=f"去嵌完成，共 {len(all_files)} 个待计算项",
        )

        wafer = _wafer_from_batch_no(batch_no)

        # 记录解压目录，供 compute_batch 使用
        batch.file_path = str(target_dir)
        db.commit()

        # 初始化虚拟文件树
        try:
            build_file_tree_from_disk(db, batch)
        except Exception:
            logger.exception("初始化虚拟文件树失败（非致命）")

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
    m = re.search(r"\.(\d+)$", batch_no)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None
