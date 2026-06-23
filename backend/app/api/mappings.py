"""对照表接口：列表 / 上传 / 详情条目 / 删除。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select

from app.api.deps import DbSession
from app.config import get_settings
from app.core.mapping import load_mapping
from app.models import Batch, Mapping, MappingEntry
from app.schemas.mapping import (
    MappingEntriesResponse,
    MappingEntryItem,
    MappingListItem,
)

router = APIRouter(prefix="/mappings", tags=["mappings"])

# name 直接拼进磁盘文件名，必须先 sanitize。允许中文、ASCII letters/digits、
# 常见标点（连字、下划线、点）；禁掉路径分隔符 / 反斜杠 / .. 以及控制字符。
_NAME_FORBIDDEN_RE = re.compile(r"[\x00-\x1f/\\]|\.\.")


@router.get("", response_model=list[MappingListItem])
def list_mappings(db: DbSession) -> list[MappingListItem]:
    stmt = select(Mapping).order_by(Mapping.uploaded_at.desc())
    mappings = db.scalars(stmt).all()
    out: list[MappingListItem] = []
    for m in mappings:
        in_use = (
            db.scalar(select(func.count()).select_from(Batch).where(Batch.mapping_id == m.id))
            or 0
        )
        out.append(
            MappingListItem(
                id=m.id,
                name=m.name,
                entry_count=m.entry_count,
                uploaded_at=m.uploaded_at,
                in_use_by_batches=in_use,
            )
        )
    return out


@router.post("", response_model=MappingListItem, status_code=status.HTTP_201_CREATED)
def upload_mapping(
    db: DbSession,
    file: Annotated[UploadFile, File(...)],
    name: Annotated[str, Form(...)],
) -> MappingListItem:
    settings = get_settings()
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".xlsx", ".xls", ".csv"):
        raise HTTPException(status_code=400, detail="仅支持 xlsx / xls / csv 文件")

    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name 不能为空")
    if _NAME_FORBIDDEN_RE.search(name):
        raise HTTPException(
            status_code=400,
            detail="name 不能包含路径分隔符 / 反斜杠 / .. 或控制字符",
        )

    existing = db.scalar(select(Mapping).where(Mapping.name == name))
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"对照表名 {name} 已存在")

    mapping_row = Mapping(name=name, file_path="", entry_count=0)
    db.add(mapping_row)
    db.flush()

    settings.mappings_dir.mkdir(parents=True, exist_ok=True)
    saved_path = settings.mappings_dir / f"{mapping_row.id}_{name}{suffix}"
    with saved_path.open("wb") as out:
        while True:
            chunk = file.file.read(4 * 1024 * 1024)
            if not chunk:
                break
            out.write(chunk)

    # 任何后续步骤失败都得 rollback + 删除磁盘文件，避免留下孤儿 .xlsx。
    try:
        try:
            entries = load_mapping(saved_path)
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"对照表解析失败: {exc!s}"
            ) from exc

        db.bulk_insert_mappings(
            MappingEntry,
            [
                {
                    "mapping_id": mapping_row.id,
                    "mark": e.mark,
                    "description": e.description,
                    "eg": e.eg,
                    "fl": e.fl,
                    "ag": e.ag,
                    "area_s11": e.area_s11,
                    "area_s22": e.area_s22,
                    "has_pf": e.has_pf,
                    "raw_tokens": list(e.raw_tokens),
                }
                for e in entries.values()
            ],
        )
        mapping_row.file_path = str(saved_path)
        mapping_row.entry_count = len(entries)
        db.commit()
        db.refresh(mapping_row)
    except Exception:
        db.rollback()
        try:
            saved_path.unlink()
        except Exception:
            pass
        raise

    return MappingListItem(
        id=mapping_row.id,
        name=mapping_row.name,
        entry_count=mapping_row.entry_count,
        uploaded_at=mapping_row.uploaded_at,
        in_use_by_batches=0,
    )


@router.get("/{mapping_id}/entries", response_model=MappingEntriesResponse)
def list_mapping_entries(
    mapping_id: int,
    db: DbSession,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=2000)] = 100,
) -> MappingEntriesResponse:
    mapping = db.get(Mapping, mapping_id)
    if mapping is None:
        raise HTTPException(status_code=404, detail=f"对照表 {mapping_id} 不存在")

    total = db.scalar(
        select(func.count())
        .select_from(MappingEntry)
        .where(MappingEntry.mapping_id == mapping_id)
    ) or 0
    stmt = (
        select(MappingEntry)
        .where(MappingEntry.mapping_id == mapping_id)
        .order_by(MappingEntry.id)
        .offset((page - 1) * size)
        .limit(size)
    )
    rows = db.scalars(stmt).all()
    items = [
        MappingEntryItem(
            mark=r.mark,
            description=r.description,
            eg=r.eg,
            fl=r.fl,
            ag=r.ag,
            area_s11=r.area_s11,
            area_s22=r.area_s22,
            has_pf=r.has_pf,
        )
        for r in rows
    ]
    return MappingEntriesResponse(total=total, page=page, size=size, items=items)


@router.delete("/{mapping_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_mapping(mapping_id: int, db: DbSession) -> None:
    mapping = db.get(Mapping, mapping_id)
    if mapping is None:
        raise HTTPException(status_code=404, detail=f"对照表 {mapping_id} 不存在")
    in_use = (
        db.scalar(select(func.count()).select_from(Batch).where(Batch.mapping_id == mapping_id))
        or 0
    )
    if in_use > 0:
        raise HTTPException(
            status_code=409,
            detail=f"对照表被 {in_use} 个批次引用，无法删除",
        )

    file_path = Path(mapping.file_path) if mapping.file_path else None
    db.delete(mapping)
    db.commit()
    if file_path and file_path.exists():
        try:
            file_path.unlink()
        except Exception:
            pass
