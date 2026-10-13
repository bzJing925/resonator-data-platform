"""SQLAlchemy ORM 模型聚合。

详见 docs/database-schema.md。
"""

from app.models.base import Base, TimestampMixin
from app.models.batch import Batch
from app.models.data_epoch import DataEpoch
from app.models.device import Device
from app.models.export_job import ExportJob
from app.models.file_node import FileNode
from app.models.lifecycle import ArtifactLease, CleanupJob, LocalReplicaPreference, Tombstone
from app.models.mapping import Mapping, MappingEntry
from app.models.process import ProcessAlnDepRun, ProcessIcpEtchRun, ProcessMoDepRun
from app.models.processing import ProcessingItem, ProcessingRun, QualityIssue, StagedDeviceRow
from app.models.source import SourceInput
from app.models.sync_operation import SyncOperation
from app.models.task import UploadTask
from app.models.upload_draft import UploadDraft

__all__ = [
    "Base",
    "TimestampMixin",
    "Batch",
    "DataEpoch",
    "Device",
    "FileNode",
    "Mapping",
    "MappingEntry",
    "ProcessAlnDepRun",
    "ProcessIcpEtchRun",
    "ProcessMoDepRun",
    "UploadTask",
    "ArtifactLease",
    "CleanupJob",
    "ExportJob",
    "LocalReplicaPreference",
    "ProcessingItem",
    "ProcessingRun",
    "QualityIssue",
    "SourceInput",
    "StagedDeviceRow",
    "SyncOperation",
    "Tombstone",
    "UploadDraft",
]

from app.models.replica import ReplicaBatch, SyncIdentity, SyncRevision, TableReceipt

__all__ += ["ReplicaBatch", "SyncIdentity", "SyncRevision", "TableReceipt"]
