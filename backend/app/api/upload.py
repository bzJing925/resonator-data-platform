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
from sqlalchemy.exc import IntegrityError

from app.api.deps import DbSession
from app.config import get_settings
from app.core.touchstone import detect_snp_type
from app.schemas.upload import DraftCommitRequest, UploadAccepted
from app.services.batch_scope import get_active_batch
from app.services.source_identity import batch_source_ref
from app.services.upload_service import (
    MappingNotFoundError,
    TaskDispatchError,
    create_batch_and_dispatch,
    create_table_batch,
)

router = APIRouter(prefix="/uploads", tags=["uploads"])


def _draft_error(exc):
    return HTTPException(
        status_code=exc.status,
        detail={
            "code": exc.code,
            "message": str(exc),
            "retryable": exc.status == 503,
            "context": exc.context,
        },
    )


def _deliver_precheck(engine, draft_id):
    from app.services.upload_draft_service import record_precheck_dispatch_failure
    from app.workers.dispatch import ConfirmedDispatchError, dispatch_precheck_job

    try:
        if dispatch_precheck_job(draft_id) is None:
            raise ConfirmedDispatchError("precheck not enqueued")
    except Exception as exc:
        record_precheck_dispatch_failure(
            engine, draft_id, confirmed=isinstance(exc, ConfirmedDispatchError)
        )
        raise HTTPException(
            503,
            detail={
                "code": "PRECHECK_DISPATCH_FAILED",
                "message": "预检查投递未确认，输入已保留",
                "retryable": True,
                "context": {"draft_id": draft_id},
            },
        ) from exc


@router.post("/prepare", status_code=202)
def prepare_upload(
    db: DbSession,
    file: Annotated[UploadFile, File(...)],
    request_key: Annotated[str, Form(...)],
    group_id: Annotated[str, Form(...)],
    mapping_id: Annotated[int, Form(...)],
    f_start_ghz: Annotated[float | None, Form()] = None,
    f_end_ghz: Annotated[float | None, Form()] = None,
    process_type: Annotated[str, Form()] = "AUTO",
    deembed: Annotated[bool, Form()] = False,
    deembed_method: Annotated[str, Form()] = "default",
    target_batch: Annotated[str, Form()] = "",
):
    from app.services.upload_draft_service import DraftError, prepare_draft
    from app.services.upload_precheck import PrecheckError

    name = Path(file.filename or "").name
    if not name or name != file.filename or "\\" in name:
        raise HTTPException(400, detail="文件名无效")
    suffix = Path(name).suffix.lower()
    if suffix not in {".zip", ".s1p", ".s2p", ".snp", ".xlsx", ".xls", ".csv"}:
        raise HTTPException(400, detail="文件格式不支持")
    directory = get_settings().uploads_dir / "drafts" / str(uuid.uuid4())
    directory.mkdir(parents=True)
    partial, saved = directory / "input.part", directory / name
    try:
        total = 0
        with partial.open("wb") as output:
            while chunk := file.file.read(8 * 1024 * 1024):
                total += len(chunk)
                if total > get_settings().UPLOAD_MAX_GB * 1024**3:
                    raise HTTPException(413, detail="上传超过容量限制")
                output.write(chunk)
        partial.rename(saved)
        engine = db.bind
        db.rollback()
        options = {
            "mapping_id": mapping_id,
            "f_start_ghz": f_start_ghz,
            "f_end_ghz": f_end_ghz,
            "process_type": process_type,
            "deembed": deembed,
            "deembed_method": deembed_method,
            "target_batch": target_batch,
        }
        draft_id, created = prepare_draft(engine, saved, name, options, request_key, group_id)
    except DraftError as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise _draft_error(exc) from exc
    except PrecheckError as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(
            422, detail={"code": exc.code, "message": str(exc), "retryable": False, "context": {}}
        ) from exc
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    if not created:
        shutil.rmtree(directory, ignore_errors=True)
    else:
        _deliver_precheck(engine, draft_id)
    return {
        "draft_id": draft_id,
        "status": "preparing",
        "poll_url": f"/api/uploads/drafts/{draft_id}",
    }


@router.get("/groups/{group_id}")
def upload_group(db: DbSession, group_id: str):
    from sqlalchemy import select

    from app.models import UploadDraft
    from app.services.upload_draft_service import draft_public

    return {
        "group_id": group_id,
        "drafts": [
            draft_public(draft, db)
            for draft in db.scalars(
                select(UploadDraft)
                .where(UploadDraft.group_id == group_id)
                .order_by(UploadDraft.created_at, UploadDraft.id)
            )
        ],
    }


@router.get("/drafts/{draft_id}")
def upload_draft(db: DbSession, draft_id: str):
    from app.models import UploadDraft
    from app.services.upload_draft_service import draft_public

    draft = db.get(UploadDraft, draft_id)
    if draft is None:
        raise HTTPException(404, detail="草稿不存在")
    return draft_public(draft, db)


@router.post("/drafts/{draft_id}/commit")
def commit_upload_draft(db: DbSession, draft_id: str, body: DraftCommitRequest):
    from app.services.upload_draft_service import DraftError, commit_draft

    engine = db.bind
    db.rollback()
    try:
        return commit_draft(
            engine,
            draft_id,
            body.request_key,
            body.check_digest,
            body.acknowledged_warning_codes,
        )
    except DraftError as exc:
        raise _draft_error(exc) from exc


@router.post("/drafts/{draft_id}/recheck", status_code=202)
def recheck_upload_draft(db: DbSession, draft_id: str, body: dict):
    from app.services.upload_draft_service import DraftError, recheck_draft
    from app.services.upload_precheck import PrecheckError

    allowed = {
        "mapping_id",
        "f_start_ghz",
        "f_end_ghz",
        "process_type",
        "deembed",
        "deembed_method",
        "target_batch",
    }
    if set(body) - allowed:
        raise HTTPException(
            400,
            detail={
                "code": "INVALID_OPTIONS",
                "message": "包含未知选项",
                "retryable": False,
                "context": {},
            },
        )
    engine = db.bind
    db.rollback()
    try:
        recheck_draft(engine, draft_id, body)
        _deliver_precheck(engine, draft_id)
    except DraftError as exc:
        raise _draft_error(exc) from exc
    except PrecheckError as exc:
        raise HTTPException(
            422, detail={"code": exc.code, "message": str(exc), "retryable": False, "context": {}}
        ) from exc
    return {"draft_id": draft_id, "status": "preparing"}


@router.delete("/drafts/{draft_id}")
def delete_upload_draft(db: DbSession, draft_id: str):
    from app.services.upload_draft_service import DraftError, delete_draft

    engine = db.bind
    db.rollback()
    try:
        return delete_draft(engine, draft_id)
    except DraftError as exc:
        raise _draft_error(exc) from exc


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
    target_batch: Annotated[str, Form()] = "",
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
        or fname_lower.endswith(".xlsx")
        or fname_lower.endswith(".xls")
        or fname_lower.endswith(".csv")
    ):
        raise HTTPException(
            status_code=400,
            detail="仅支持 .zip / .s1p / .s2p / .snp / .xlsx / .xls / .csv 文件",
        )
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
        fname_lower.endswith(".s1p") or fname_lower.endswith(".s2p") or fname_lower.endswith(".snp")
    )
    is_table = fname_lower.endswith((".xlsx", ".xls", ".csv"))
    batch_no = Path(file.filename).stem
    if not batch_no:
        raise HTTPException(status_code=400, detail="文件名为空，无法解析批次号")

    # 表格关联目标必须是已同步的服务器副本：上传时即校验，避免落库后每次
    # 同步都因目标不存在/非副本而 409 重放。
    link_target = None
    if is_table and target_batch.strip():
        from sqlalchemy import select

        from app.models import Batch, ReplicaBatch

        link_target = db.scalar(select(Batch).where(Batch.batch_no == target_batch.strip()))
        if link_target is None:
            raise HTTPException(
                status_code=400,
                detail=f"关联目标批次 {target_batch.strip()} 不存在；留空则作为独立指标批次",
            )
        get_active_batch(db, target_batch.strip())
        if db.get(ReplicaBatch, link_target.id) is None:
            raise HTTPException(
                status_code=400,
                detail="关联目标必须是已同步的服务器批次（批次列表带 ☁️ 服务器标记），请先同步",
            )

    month_dir = settings.uploads_dir / datetime.now(UTC).strftime("%Y-%m")
    try:
        month_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"无法创建上传目录 {month_dir}：{exc}。"
                f"请检查 DATA_ROOT ({settings.DATA_ROOT}) 是否存在且当前用户可写。"
            ),
        ) from exc
    saved_name = f"{uuid.uuid4().hex}{Path(file.filename).suffix.lower() if is_table else '.zip'}"
    saved_path = month_dir / saved_name

    max_bytes = settings.UPLOAD_MAX_GB * 1024**3
    tmp_dir: Path | None = None

    try:
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
                        raise HTTPException(
                            status_code=413,
                            detail=f"文件超过上限 {settings.UPLOAD_MAX_GB} GB",
                        )
                    out.write(chunk)
    except HTTPException:
        # 清理可能产生的临时文件/半成品 zip
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        saved_path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        saved_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500,
            detail=(
                f"保存上传文件失败：{exc}。"
                f"请检查 DATA_ROOT ({settings.DATA_ROOT}) 是否有足够磁盘空间且可写。"
            ),
        ) from exc
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # zip 已经在盘上了；下面任何 DB 失败都得把 zip 删掉，避免
    # 并发上传同名文件触发 batch_no unique 冲突时孤儿 zip 占盘。
    try:
        if is_table:
            task = create_table_batch(
                db,
                table_path=saved_path,
                batch_no=batch_no,
                mapping_id=mapping_id,
                deembedded=deembed,
            )
        else:
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
        if is_table and task is not None and link_target is not None:
            from sqlalchemy import select

            from app.models import Batch, ReplicaBatch, SyncIdentity

            imported = db.scalar(select(Batch).where(Batch.batch_no == task.batch_no))
            replica = db.get(ReplicaBatch, link_target.id)
            import json

            db.add(
                SyncIdentity(
                    key=f"table-target:{imported.id}",
                    value=json.dumps(
                        {
                            "batch_no": link_target.batch_no,
                            "revision": replica.revision,
                            "source_ref": batch_source_ref(db, link_target),
                        }
                    ),
                )
            )
            db.commit()
        if task is None:
            raise HTTPException(status_code=500, detail="创建批次失败")
    except HTTPException:
        raise
    except IntegrityError:
        raise
    except MappingNotFoundError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TaskDispatchError as exc:
        raise HTTPException(status_code=503, detail=f"任务队列不可用: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"创建批次失败: {exc!s}") from exc

    return UploadAccepted(
        task_id=str(task.id),
        batch_no=task.batch_no,
        status=task.status,
        stream_url=f"/api/tasks/{task.id}/stream",
    )


@router.post("/chunk", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def chunk_upload() -> dict:
    raise HTTPException(status_code=501, detail="分块上传暂未实现")
