"""file_nodes 表：批次虚拟文件树。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.batch import Batch


class FileNode(Base, TimestampMixin):
    """批次内虚拟文件树节点。

    一个 batch 对应一棵文件树：
      - root：根节点，每个 batch 有且仅有一个
      - zip：对应一次上传的原始压缩包（当前一个 batch 一个 zip）
      - folder：用户新建的虚拟文件夹
      - file：指向磁盘上真实 .s1p/.s2p/.snp 文件的虚拟节点

    真实文件路径通过 `relpath` 保存（相对于 DATA_ROOT/files/{batch_no}），
    重命名/移动/排序均只改本表，不改磁盘文件，从而保持 devices.s_param_path 稳定。
    """

    __tablename__ = "file_nodes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("batches.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("file_nodes.id", ondelete="CASCADE"), nullable=True
    )
    node_type: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    relpath: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_zip: Mapped[str | None] = mapped_column(Text, nullable=True)

    batch: Mapped[Batch] = relationship(back_populates="file_nodes")
    parent: Mapped[FileNode | None] = relationship(back_populates="children", remote_side=[id])
    children: Mapped[list[FileNode]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "node_type IN ('root','zip','folder','file')",
            name="ck_file_node_type",
        ),
        Index(
            "idx_file_nodes_batch_parent_order",
            "batch_id",
            "parent_id",
            "sort_order",
            "is_deleted",
        ),
        Index("idx_file_nodes_batch_type", "batch_id", "node_type"),
        Index(
            "idx_file_nodes_relpath",
            "batch_id",
            "relpath",
        ),
    )
