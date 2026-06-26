"""单文件计算与文件列表接口。

- GET  /api/files?batch_no=...        列出批次已解压的 .s1p 文件
- POST /api/files/compute             对单个文件执行指标计算并入库/更新
"""

from __future__ import annotations

import gzip
import logging
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import numpy as np
import skrf
import zipstream
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, text, update

from app.api.deps import DEVICE_COLUMNS, DbSession
from app.config import get_settings
from app.core.extract import ExtractError, extract_resonator_params
from app.core.mapping import load_mapping
from app.core.touchstone import split_s2p_to_s1p
from app.models import Batch, Device, Mapping
from app.schemas.file import (
    BatchFileItem,
    ComputeFileRequest,
    ComputeFileResponse,
    DownloadZipRequest,
    FileCurveResponse,
    SplitS2PRequest,
)

_PARAM_CHOICES = ("s11_db", "s11_phase", "s11_re_im", "z_mag_db", "z_phase")

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/files", tags=["files"])


def _batch_files_dir(batch_no: str) -> Path:
    return get_settings().files_dir / batch_no


def _safe_resolve(base_dir: Path, relpath: str) -> Path:
    """把相对路径解析为 base_dir 下的真实路径，并防止目录穿越。"""
    target = (base_dir / relpath).resolve()
    base = base_dir.resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="非法文件路径") from exc
    return target


def _find_actual_path(base_dir: Path, relpath: str) -> Path:
    """解析相对路径；若原文件不存在但存在 .gz 版本，则返回 .gz 路径。"""
    target = _safe_resolve(base_dir, relpath)
    if target.exists():
        return target
    gz_target = target.with_suffix(target.suffix + ".gz")
    if gz_target.exists():
        return gz_target
    raise HTTPException(status_code=404, detail=f"文件不存在: {relpath}")


def _copy_maybe_gz(src: Path, dst: Path) -> None:
    """复制文件；若源为 .gz 则先解压。"""
    if src.suffix.lower() == ".gz":
        with gzip.open(src, "rb") as f_in, open(dst, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    else:
        shutil.copy2(src, dst)


def _read_network(target_path: Path, process_type: str = "S1P") -> skrf.Network:
    """读取 s1p/s2p/snp 文件；.snp 按 process_type 临时改名后读取。

    支持透明读取 .s1p.gz / .s2p.gz：先解压到临时文件再交给 skrf。
    """
    suffix = target_path.suffix.lower()
    is_gz = False
    real_suffix = suffix
    if suffix == ".gz":
        is_gz = True
        real_suffix = Path(target_path.stem).suffix.lower()

    if real_suffix == ".snp":
        new_ext = ".s1p" if process_type == "S1P" else ".s2p"
        tmp_dir = Path(tempfile.mkdtemp(prefix="aln_snp_"))
        tmp_path = tmp_dir / (target_path.stem + new_ext)
        _copy_maybe_gz(target_path, tmp_path)
        try:
            return skrf.Network(str(tmp_path))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    if is_gz:
        tmp_dir = Path(tempfile.mkdtemp(prefix="aln_gz_"))
        tmp_path = tmp_dir / target_path.stem
        _copy_maybe_gz(target_path, tmp_path)
        try:
            return skrf.Network(str(tmp_path))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return skrf.Network(str(target_path))


@router.get("", response_model=list[BatchFileItem])
def list_batch_files(
    db: DbSession,
    batch_no: Annotated[str, Query(..., description="批次号")],
    include_snp: Annotated[bool, Query()] = False,
) -> list[BatchFileItem]:
    """列出批次解压后的 snp 文件；默认仅 .s1p，include_snp=true 时包含 .s2p/.snp。"""
    batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    if batch is None:
        raise HTTPException(status_code=404, detail=f"批次 {batch_no} 不存在")

    base_dir = _batch_files_dir(batch_no)
    if not base_dir.exists():
        return []

    # 已计算 device 的 (s_param_path, s_param_port) → device_id
    existing = {
        (d.s_param_path or "", d.s_param_port): d.id
        for d in db.scalars(select(Device).where(Device.batch_id == batch.id)).all()
    }

    patterns = ["*.s1p", "*.s1p.gz"]
    if include_snp:
        patterns.extend(["*.s2p", "*.s2p.gz", "*.snp"])

    files: list[BatchFileItem] = []
    seen: set[str] = set()
    for pattern in patterns:
        for p in sorted(base_dir.rglob(pattern)):
            if not p.is_file():
                continue
            try:
                relpath = str(p.relative_to(base_dir))
            except ValueError:
                continue
            if relpath in seen:
                continue
            seen.add(relpath)
            stat = p.stat()
            deembedded = "S11_de" in relpath or "S22_de" in relpath or relpath.endswith("_de.s1p")
            device_id = existing.get((relpath, "S11")) or existing.get((relpath, "S22"))
            files.append(
                BatchFileItem(
                    relpath=relpath,
                    name=p.name,
                    size=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                    deembedded=deembedded,
                    computed=device_id is not None,
                    device_id=device_id,
                )
            )
    return files


@router.post("/split-s2p")
def split_s2p_files(db: DbSession, body: SplitS2PRequest) -> Response:
    """把选中的 .s2p 文件拆成 S11/S22 两个 .s1p 并流式打包返回。"""
    batch = db.scalar(select(Batch).where(Batch.batch_no == body.batch_no))
    if batch is None:
        raise HTTPException(status_code=404, detail=f"批次 {body.batch_no} 不存在")

    base_dir = _batch_files_dir(body.batch_no)
    if not base_dir.exists():
        raise HTTPException(status_code=404, detail="批次解压目录不存在")

    selected: list[tuple[Path, str]] = []
    for relpath in body.relpaths:
        target = _safe_resolve(base_dir, relpath)
        if not target.is_file() or target.suffix.lower() != ".s2p":
            raise HTTPException(
                status_code=400, detail=f"非法或不存在文件（必须为 .s2p）: {relpath}"
            )
        selected.append((target, relpath))

    if not selected:
        raise HTTPException(status_code=400, detail="未选择任何 .s2p 文件")

    tmp_root = Path(tempfile.mkdtemp(prefix="aln_split_"))
    s11_root = tmp_root / "s11"
    s22_root = tmp_root / "s22"

    zs = zipstream.ZipStream(compress_type=zipstream.ZIP_DEFLATED)
    for target, relpath in selected:
        try:
            split = split_s2p_to_s1p(
                target,
                out_dir_s11=s11_root,
                out_dir_s22=s22_root,
                lowercase=body.lowercase,
            )
        except Exception as exc:
            shutil.rmtree(tmp_root, ignore_errors=True)
            raise HTTPException(
                status_code=422, detail=f"拆分失败 {relpath}: {exc}"
            ) from exc

        # 保持原相对路径目录结构，仅把 .s2p 替换为 _s11/_s22.s1p
        rel_stem = relpath[:-4]  # 去掉 .s2p
        s11_arc = f"{rel_stem}{'_s11' if body.lowercase else '_S11'}.s1p"
        s22_arc = f"{rel_stem}{'_s22' if body.lowercase else '_S22'}.s1p"
        zs.add_path(str(split.s11_path), arcname=s11_arc, recurse=False)
        zs.add_path(str(split.s22_path), arcname=s22_arc, recurse=False)

    background = BackgroundTasks()
    background.add_task(lambda: shutil.rmtree(tmp_root, ignore_errors=True))

    filename = f"{body.batch_no}_split_s2p.zip"
    return StreamingResponse(
        zs,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        background=background,
    )


@router.post("/download-zip")
def download_files_zip(db: DbSession, body: DownloadZipRequest) -> Response:
    """把选中的 snp 文件打包成 zip 流式下载；relpaths 为空时下载该批次全部文件。"""
    batch = db.scalar(select(Batch).where(Batch.batch_no == body.batch_no))
    if batch is None:
        raise HTTPException(status_code=404, detail=f"批次 {body.batch_no} 不存在")

    base_dir = _batch_files_dir(body.batch_no)
    if not base_dir.exists():
        raise HTTPException(status_code=404, detail="批次解压目录不存在")

    selected: list[tuple[Path, str]] = []

    if body.relpaths:
        for relpath in body.relpaths:
            target = _find_actual_path(base_dir, relpath)
            if target.suffix.lower() == ".gz":
                actual_suffix = Path(target.stem).suffix.lower()
            else:
                actual_suffix = target.suffix.lower()
            if actual_suffix not in {".s1p", ".s2p", ".snp"}:
                raise HTTPException(
                    status_code=400, detail=f"非法文件类型: {relpath}"
                )
            selected.append((target, relpath))
    else:
        for p in sorted(base_dir.rglob("*")):
            if not p.is_file():
                continue
            if p.suffix.lower() == ".gz":
                actual_suffix = Path(p.stem).suffix.lower()
            else:
                actual_suffix = p.suffix.lower()
            if actual_suffix in {".s1p", ".s2p", ".snp"}:
                selected.append((p, str(p.relative_to(base_dir))))

    if not selected:
        raise HTTPException(status_code=404, detail="没有可下载的 snp 文件")

    zs = zipstream.ZipStream(compress_type=zipstream.ZIP_DEFLATED)
    for target, arcname in selected:
        zs.add_path(str(target), arcname=arcname, recurse=False)

    filename = f"{body.batch_no}_files.zip"
    return StreamingResponse(
        zs,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/curve", response_model=FileCurveResponse)
def get_file_curve(
    db: DbSession,
    batch_no: Annotated[str, Query(...)],
    relpath: Annotated[str, Query(...)],
    param: Annotated[str, Query()] = "z_mag_db",
) -> FileCurveResponse:
    """直接从批次解压目录读取指定文件的 S 参数 / 阻抗曲线（无需先入库）。"""
    if param not in _PARAM_CHOICES:
        raise HTTPException(
            status_code=400, detail=f"param 必须是 {','.join(_PARAM_CHOICES)} 之一"
        )

    batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    if batch is None:
        raise HTTPException(status_code=404, detail=f"批次 {batch_no} 不存在")

    base_dir = _batch_files_dir(batch_no)
    target_path = _find_actual_path(base_dir, relpath)

    try:
        net = _read_network(target_path, batch.process_type or "S1P")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"读取 S 参数文件失败: {exc}") from exc

    freq_ghz = (net.f / 1e9).tolist()
    s = net.s[:, 0, 0]

    if param == "s11_db":
        values = (20 * np.log10(np.maximum(np.abs(s), 1e-12))).tolist()
    elif param == "s11_phase":
        values = [float(v) for v in np.degrees(np.unwrap(np.angle(s)))]
    elif param == "s11_re_im":
        return FileCurveResponse(
            batch_no=batch_no,
            relpath=relpath,
            param=param,
            freq_ghz=freq_ghz,
            values=[],
            values_re=np.real(s).tolist(),
            values_im=np.imag(s).tolist(),
        )
    elif param == "z_mag_db":
        z0 = net.z0[0, 0]
        z = z0 * (1 + s) / (1 - s)
        values = (20 * np.log10(np.maximum(np.abs(z), 1e-12))).tolist()
    elif param == "z_phase":
        z0 = net.z0[0, 0]
        z = z0 * (1 + s) / (1 - s)
        values = [float(v) for v in np.degrees(np.unwrap(np.angle(z)))]
    else:
        raise HTTPException(status_code=400, detail="param 不支持")

    return FileCurveResponse(
        batch_no=batch_no,
        relpath=relpath,
        param=param,
        freq_ghz=freq_ghz,
        values=values,
    )


@router.post("/compute", response_model=ComputeFileResponse)
def compute_single_file(db: DbSession, body: ComputeFileRequest) -> ComputeFileResponse:
    """对指定批次内的单个 .s1p 文件执行指标计算，并写入/更新 devices 表。"""
    batch = db.scalar(select(Batch).where(Batch.batch_no == body.batch_no))
    if batch is None:
        raise HTTPException(status_code=404, detail=f"批次 {body.batch_no} 不存在")

    base_dir = _batch_files_dir(body.batch_no)
    target_path = _find_actual_path(base_dir, body.relpath)

    mapping_row = db.get(Mapping, batch.mapping_id) if batch.mapping_id else None
    mapping_dict = load_mapping(mapping_row.file_path) if mapping_row else {}

    wafer = None
    m = re.search(r"\.(\d+)$", body.batch_no)
    if m:
        try:
            wafer = int(m.group(1))
        except ValueError:
            pass

    try:
        row = extract_resonator_params(
            target_path,
            mapping=mapping_dict,
            wafer=wafer,
            s_param_relpath=body.relpath,
            deembedded=body.deembedded,
            f_start_ghz=body.f_start_ghz,
            f_end_ghz=body.f_end_ghz,
            skip_validation=True,
        )
    except ExtractError as exc:
        raise HTTPException(status_code=422, detail=f"指标计算失败: {exc}") from exc
    except Exception as exc:
        logger.exception("单文件计算异常 %s", body.relpath)
        raise HTTPException(status_code=500, detail=f"计算异常: {exc}") from exc

    row["batch_id"] = batch.id

    # 按 (batch_id, s_param_path, s_param_port) 查找是否已存在，存在则更新，否则插入
    existing = db.scalar(
        select(Device).where(
            Device.batch_id == batch.id,
            Device.s_param_path == body.relpath,
            Device.s_param_port == row.get("s_param_port", "S11"),
        )
    )
    if existing is not None:
        for col in DEVICE_COLUMNS:
            if col == "id":
                continue
            if col in row:
                setattr(existing, col, row[col])
        device = existing
    else:
        device = Device(**{col: row.get(col) for col in DEVICE_COLUMNS if col != "id"})
        db.add(device)
    db.commit()
    db.refresh(device)

    # 同步批次统计
    device_count = (
        db.scalar(select(func.count(Device.id)).where(Device.batch_id == batch.id)) or 0
    )
    db.execute(
        update(Batch)
        .where(Batch.id == batch.id)
        .values(device_count=device_count)
    )
    db.commit()
    try:
        db.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_batch_stats"))
        db.commit()
    except Exception:
        logger.exception("刷新物化视图失败（非致命）")

    metrics = {col: getattr(device, col) for col in DEVICE_COLUMNS if col != "batch_id"}
    return ComputeFileResponse(
        device_id=device.id,
        batch_no=body.batch_no,
        relpath=body.relpath,
        metrics=metrics,
    )
