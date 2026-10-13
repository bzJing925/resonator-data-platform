"""运行模式能力与同步确认状态。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CapabilityFeatures(BaseModel):
    upload_precheck: bool
    processing_reports: bool
    projects: bool
    full_export_csv: bool
    full_export_xlsx: bool
    batch_compare: bool
    trash: bool
    replication_v3: bool
    remote_delete: bool
    credential_storage: bool


class Capabilities(BaseModel):
    schema_version: Literal[1]
    mode: Literal["server", "desktop", "static"]
    database: Literal["sqlite", "postgresql", "duckdb"]
    protocols: list[str]
    features: CapabilityFeatures
    limits: dict[str, int | float | str | bool | None]

    @model_validator(mode="after")
    def validate_runtime_database(self) -> Capabilities:
        if self.mode == "static" and self.database != "duckdb":
            raise ValueError("static mode must use duckdb")
        if self.mode == "desktop" and self.database != "sqlite":
            raise ValueError("desktop mode must use sqlite")
        if self.mode == "server" and self.database not in {"sqlite", "postgresql"}:
            raise ValueError("server mode supports sqlite or postgresql")
        return self


class SyncStatus(BaseModel):
    target: Literal["server", "web"]
    state: Literal["local_only", "pending", "syncing", "confirmed", "conflict", "failed", "unknown"]
    confirmed_revision: int | None = Field(ge=0)
    pending_revision: int | None = Field(ge=0)
    last_success_at: datetime | None
    error_code: str | None
    retryable: bool
