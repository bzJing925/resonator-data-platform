"""批次号重命名服务。"""

from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Batch, FileNode, UploadTask

logger = logging.getLogger(__name__)

# 禁止出现在批次号中的字符：路径分隔符、空字符、控制字符
_INVALID_BATCH_NO_RE = re.compile(r"[\x00-\x1f\\/<>|:\"?*]|^\s*$|^\.+$")


class BatchRenameError(Exception):
    """重命名失败基类。"""


class BatchNotFoundError(BatchRenameError):
    """原批次不存在。"""


class BatchRenameDuplicateError(BatchRenameError):
    """目标批次号已存在。"""


class BatchRenameActiveTaskError(BatchRenameError):
    """批次有进行中的任务。"""


class BatchRenameInvalidNameError(BatchRenameError):
    """新批次号格式无效。"""


def _validate_new_batch_no(new_batch_no: str) -> str:
    """校验并返回整理后的新批次号。"""
    if not isinstance(new_batch_no, str):
        raise BatchRenameInvalidNameError("新批次号必须是字符串")
    name = new_batch_no.strip()
    if not name:
        raise BatchRenameInvalidNameError("新批次号不能为空")
    if len(name) > 128:
        raise BatchRenameInvalidNameError("新批次号长度不能超过 128")
    if _INVALID_BATCH_NO_RE.search(name):
        raise BatchRenameInvalidNameError("新批次号包含非法字符")
    return name


def rename_batch(db: Session, old_batch_no: str, new_batch_no: str) -> str:
    """将批次从 old_batch_no 重命名为 new_batch_no。

    更新内容：
    - batches.batch_no
    - upload_tasks.batch_no（如果存在）
    - file_nodes 根节点 name
    磁盘目录与原始ZIP路径保持不变。

    异常：
    - BatchNotFoundError：原批次不存在
    - BatchRenameDuplicateError：目标名称已存在
    - BatchRenameActiveTaskError：有关联任务正在运行/排队
    - BatchRenameInvalidNameError：新名称格式无效
    """
    new_batch_no = _validate_new_batch_no(new_batch_no)
    if new_batch_no == old_batch_no:
        raise BatchRenameInvalidNameError("新批次号不能与旧批次号相同")

    batch = db.scalar(select(Batch).where(Batch.batch_no == old_batch_no))
    if batch is None or batch.deleted_at is not None:
        raise BatchNotFoundError(f"批次 {old_batch_no} 不存在")

    # 目标名称唯一性
    existing = db.scalar(select(Batch).where(Batch.batch_no == new_batch_no))
    if existing is not None:
        raise BatchRenameDuplicateError(f"批次号 {new_batch_no} 已存在")

    # 检查进行中的任务
    if batch.active_run_id is not None:
        raise BatchRenameActiveTaskError(f"批次 {old_batch_no} 有进行中的任务")
    if batch.task_id is not None:
        task = db.get(UploadTask, batch.task_id)
        if task is not None and task.status in ("pending", "running", "cancel_requested"):
            raise BatchRenameActiveTaskError(f"批次 {old_batch_no} 有进行中的任务")

    try:
        batch.batch_no = new_batch_no

        if batch.task_id is not None:
            task = db.get(UploadTask, batch.task_id)
            if task is not None:
                task.batch_no = new_batch_no

        # 更新虚拟文件树根节点名称
        root = db.scalar(
            select(FileNode).where(
                FileNode.batch_id == batch.id,
                FileNode.node_type == "root",
            )
        )
        if root is not None:
            root.name = new_batch_no

        db.commit()
    except Exception:
        db.rollback()
        raise

    return new_batch_no
