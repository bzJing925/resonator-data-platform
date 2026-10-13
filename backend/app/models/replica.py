"""内网副本元数据，与业务表分离以支持既有数据库原位升级。"""
from sqlalchemy import JSON, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ReplicaBatch(Base):
    __tablename__ = "replica_batches"
    batch_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    server_id: Mapped[str] = mapped_column(Text)
    remote_id: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    device_ids: Mapped[dict] = mapped_column(JSON, default=dict)


class SyncIdentity(Base):
    __tablename__ = "sync_identity"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class SyncRevision(Base):
    __tablename__ = "sync_revisions"
    batch_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class TableReceipt(Base):
    __tablename__ = "table_receipts"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    digest: Mapped[str] = mapped_column(Text)
    batch_id: Mapped[int] = mapped_column(Integer)
