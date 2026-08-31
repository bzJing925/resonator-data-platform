"""虚拟文件树服务。

把文件/目录相关的纯数据处理从 API 层抽出来，供 routes 和 workers 共用。
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Batch, FileNode

logger = logging.getLogger(__name__)


def batch_files_dir(batch_no: str) -> Path:
    return get_settings().files_dir / batch_no


def get_or_create_root_node(db: Session, batch: Batch) -> FileNode:
    root = db.scalar(
        select(FileNode).where(FileNode.batch_id == batch.id, FileNode.node_type == "root")
    )
    if root is None:
        root = FileNode(
            batch_id=batch.id,
            parent_id=None,
            node_type="root",
            name=batch.batch_no,
            sort_order=0,
        )
        db.add(root)
        db.flush()
    return root


def zip_node_name(batch: Batch) -> str:
    """根据 batch 的原始 zip 路径生成 zip 节点显示名。"""
    raw = batch.raw_zip_path or batch.file_path or ""
    name = Path(raw).name
    if not name:
        name = f"{batch.batch_no}.zip"
    if not name.lower().endswith(".zip"):
        name = f"{name}.zip"
    return name


def scan_disk_files(base_dir: Path) -> list[tuple[Path, str]]:
    """扫描 base_dir 下所有 .s1p/.s2p/.snp 文件，返回 (绝对路径, relpath) 列表。"""
    found: list[tuple[Path, str]] = []
    if not base_dir.exists():
        return found
    for pattern in ("*.s1p", "*.s1p.gz", "*.s2p", "*.s2p.gz", "*.snp"):
        for p in sorted(base_dir.rglob(pattern)):
            if p.is_file():
                try:
                    relpath = str(p.relative_to(base_dir))
                except ValueError:
                    continue
                found.append((p, relpath))
    return found


def build_file_tree_from_disk(db: Session, batch: Batch) -> None:
    """根据磁盘目录结构初始化 file_nodes 虚拟树。

    幂等：已存在的 file 节点不会被重复创建，只会被重新挂到正确的父级并恢复 is_deleted。
    """
    root = get_or_create_root_node(db, batch)

    zip_name = zip_node_name(batch)
    zip_node = db.scalar(
        select(FileNode).where(
            FileNode.batch_id == batch.id,
            FileNode.node_type == "zip",
            FileNode.parent_id == root.id,
        )
    )
    if zip_node is None:
        zip_node = FileNode(
            batch_id=batch.id,
            parent_id=root.id,
            node_type="zip",
            name=zip_name,
            source_zip=zip_name,
            sort_order=0,
        )
        db.add(zip_node)
        db.flush()

    base_dir = batch_files_dir(batch.batch_no)
    disk_files = scan_disk_files(base_dir)

    folder_nodes: dict[str, FileNode] = {"": zip_node}
    folder_order = 0
    file_order = 0

    existing_files = {
        n.relpath: n
        for n in db.scalars(
            select(FileNode).where(
                FileNode.batch_id == batch.id,
                FileNode.node_type == "file",
            )
        ).all()
    }

    for _abs_path, relpath in disk_files:
        parts = Path(relpath).parts
        parent = zip_node
        current_rel = ""
        for part in parts[:-1]:
            current_rel = f"{current_rel}/{part}".lstrip("/") if current_rel else part
            if current_rel not in folder_nodes:
                folder = FileNode(
                    batch_id=batch.id,
                    parent_id=parent.id,
                    node_type="folder",
                    name=part,
                    sort_order=folder_order,
                )
                folder_order += 1
                db.add(folder)
                db.flush()
                folder_nodes[current_rel] = folder
            parent = folder_nodes[current_rel]

        if relpath in existing_files:
            # 已存在的 file 节点不再被磁盘扫描覆盖 parent/sort_order/name，
            # 否则用户在前端做的移动、排序、删除会被下一次 list 请求重置。
            continue
        file_node = FileNode(
            batch_id=batch.id,
            parent_id=parent.id,
            node_type="file",
            name=parts[-1],
            relpath=relpath,
            sort_order=file_order,
        )
        db.add(file_node)
        file_order += 1

    db.commit()
