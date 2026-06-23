"""上传接口：POST /api/uploads。

实际创建批次与投递 Celery 链的逻辑已抽到 app.services.upload_service，
HTTP 上传只负责接收文件流并保存成本地 zip。
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.api.deps import DbSession
from app.config import get_settings
from app.core.touchstone import detect_snp_type
from app.schemas.upload import UploadAccepted
from app.services.upload_service import create_batch_and_dispatch

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.post("", response_model=UploadAccepted, status_code=status.HTTP_202_ACCEPTED)
def create_upload(
    db: DbSession,
    file: Annotated[UploadFile, File(...)],
    mapping_id: Annotated[int, Form(...)],
    f_start_ghz: Annotated[float | None, Form()] = None,
    f_end_ghz: Annotated[float | None, Form()] = None,
    process_type: Annotated[str, Form()] = "AUTO",
    deembed: Annotated[bool, Form()] = False,
    deembed_method: Annotated[str, Form()] = "default",
) -> UploadAccepted:
    settings = get_settings()

    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件")
    fname_lower = file.filename.lower()
    if not (
        fname_lower.endswith(".zip")
        or fname_lower.endswith(".s1p")
        or fname_lower.endswith(".s2p")
        or fname_lower.endswith(".snp")
    ):
        raise HTTPException(status_code=400, detail="仅支持 .zip / .s1p / .s2p / .snp 文件")
    if process_type not in ("AUTO", "S1P", "S2P", "BOTH"):
        raise HTTPException(
            status_code=400, detail="process_type 必须是 AUTO / S1P / S2P / BOTH 之一"
        )
    valid_methods = {"default", "original", "gsg100", "vz", "basic"}
    if deembed and deembed_method not in valid_methods:
        raise HTTPException(
            status_code=400,
            detail=f"去嵌方法必须是 {', '.join(sorted(valid_methods))} 之一",
        )

    is_snp = (
        fname_lower.endswith(".s1p")
        or fname_lower.endswith(".s2p")
        or fname_lower.endswith(".snp")
    )
    batch_no = Path(file.filename).stem
    if not batch_no:
        raise HTTPException(status_code=400, detail="文件名为空，无法解析批次号")

    month_dir = settings.uploads_dir / datetime.now(UTC).strftime("%Y-%m")
    month_dir.mkdir(parents=True, exist_ok=True)
    saved_name = f"{uuid.uuid4().hex}.zip"
    saved_path = month_dir / saved_name

    max_bytes = settings.UPLOAD_MAX_GB * 1024**3

    if is_snp:
        # .s1p/.s2p/.snp 自动打包为 zip
        tmp_dir = Path(tempfile.mkdtemp(prefix="aln_snp_"))
        snp_path = tmp_dir / file.filename
        written = 0
        with snp_path.open("wb") as out:
            while True:
                chunk = file.file.read(8 * 1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    out.close()
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件超过上限 {settings.UPLOAD_MAX_GB} GB",
                    )
                out.write(chunk)
        # .snp 是通用扩展名，自动识别为 S1P / S2P；AUTO 模式下按文件内容判断
        arcname = file.filename
        if fname_lower.endswith(".snp"):
            if process_type in ("AUTO", "BOTH"):
                detected = detect_snp_type(snp_path)
                ext = ".s1p" if detected == "S1P" else ".s2p"
            else:
                ext = ".s1p" if process_type == "S1P" else ".s2p"
            arcname = Path(file.filename).with_suffix(ext).name
        with zipfile.ZipFile(saved_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(snp_path, arcname=arcname)
        shutil.rmtree(tmp_dir, ignore_errors=True)
    else:
        written = 0
        with saved_path.open("wb") as out:
            while True:
                chunk = file.file.read(8 * 1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    out.close()
                    saved_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件超过上限 {settings.UPLOAD_MAX_GB} GB",
                    )
                out.write(chunk)

    # zip 已经在盘上了；下面任何 DB 失败都得把 zip 删掉，避免
    # 并发上传同名文件触发 batch_no unique 冲突时孤儿 zip 占盘。
    try:
        task = create_batch_and_dispatch(
            db,
            zip_path=saved_path,
            batch_no=batch_no,
            mapping_id=mapping_id,
            source="http",
            f_start_ghz=f_start_ghz,
            f_end_ghz=f_end_ghz,
            deembed=deembed,
            deembed_method=deembed_method,
            process_type=process_type,
        )
        if task is None:
            # 重复批次：删除刚保存的 zip
            saved_path.unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail=f"批次 {batch_no} 已存在")
    except HTTPException:
        raise
    except Exception as exc:
        saved_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"创建批次失败: {exc!s}") from exc

    return UploadAccepted(
        task_id=str(task.id),
        batch_no=batch_no,
        status=task.status,
        stream_url=f"/api/tasks/{task.id}/stream",
    )


@router.post("/chunk", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def chunk_upload() -> dict:
    raise HTTPException(status_code=501, detail="分块上传暂未实现")
