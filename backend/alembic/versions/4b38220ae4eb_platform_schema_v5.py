"""Add processing, source identity, lifecycle, draft and export schema.

Revision ID: 4b38220ae4eb
Revises: 2026_09_15_120000
Create Date: 2026-10-09 11:38:27.340695
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "4b38220ae4eb"
down_revision: str | None = "2026_09_15_120000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PROCESSING_STATUSES = "'pending','running','success','failed','cancel_requested','cancelled'"


def upgrade() -> None:
    op.add_column("batches", sa.Column("origin_id", sa.Text(), nullable=True))
    op.add_column("batches", sa.Column("source_batch_id", sa.Text(), nullable=True))
    op.add_column("batches", sa.Column("active_run_id", sa.Text(), nullable=True))
    op.add_column(
        "batches",
        sa.Column("result_revision", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("batches", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("batches", sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint(
        "uq_batch_source_identity", "batches", ["origin_id", "source_batch_id"]
    )
    op.create_index("idx_batches_deleted_at", "batches", ["deleted_at"])

    op.add_column("devices", sa.Column("source_device_key", sa.Text(), nullable=True))
    op.create_unique_constraint("uq_device_source_key", "devices", ["source_device_key"])

    with op.batch_alter_table("upload_tasks") as batch_op:
        batch_op.drop_constraint("ck_uptask_status", type_="check")
        batch_op.create_check_constraint("ck_uptask_status", f"status IN ({_PROCESSING_STATUSES})")

    op.create_table(
        "processing_runs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "task_id",
            sa.BigInteger(),
            sa.ForeignKey("upload_tasks.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("batch_id", sa.BigInteger(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("attempt", sa.SmallInteger(), nullable=False),
        sa.Column(
            "retry_of_run_id", sa.Text(), sa.ForeignKey("processing_runs.id", ondelete="SET NULL")
        ),
        sa.Column("base_result_revision", sa.Integer(), nullable=False),
        sa.Column("base_source_revision", sa.Integer()),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("mapping_digest", sa.Text()),
        sa.Column("algorithm_digest", sa.Text()),
        sa.Column("input_digest", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("quality_status", sa.Text(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.Text()),
        sa.Column("stage_dir", sa.Text()),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("dispatch_phase", sa.Text(), nullable=False),
        sa.Column("executor_ids", sa.JSON(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("published_count", sa.Integer(), nullable=False),
        sa.UniqueConstraint("task_id", name="uq_processing_run_task"),
        sa.CheckConstraint(f"status IN ({_PROCESSING_STATUSES})", name="ck_processing_run_status"),
        sa.CheckConstraint(
            "quality_status IN ('unknown','complete','partial','invalid')",
            name="ck_processing_run_quality_status",
        ),
        sa.CheckConstraint(
            "dispatch_phase IN ('queued','extract','extract_handoff',"
            "'compute','publish','finished')",
            name="ck_processing_run_dispatch_phase",
        ),
        sa.CheckConstraint(
            "kind IN ('upload','reextract','redeembed','recompute','remap')",
            name="ck_processing_run_kind",
        ),
        sa.CheckConstraint("attempt >= 1", name="ck_processing_run_attempt"),
        sa.CheckConstraint("published_count >= 0", name="ck_processing_run_published_count"),
    )
    op.create_index(
        "idx_processing_runs_status_heartbeat", "processing_runs", ["status", "heartbeat_at"]
    )
    op.create_index("idx_processing_runs_batch_status", "processing_runs", ["batch_id", "status"])

    op.create_table(
        "processing_items",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Text(),
            sa.ForeignKey("processing_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_device_key", sa.Text()),
        sa.Column("source_member", sa.Text(), nullable=False),
        sa.Column("port", sa.Text()),
        sa.Column("port_key", sa.Text(), server_default="", nullable=False),
        sa.Column("unit_kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text()),
        sa.Column("result_digest", sa.Text()),
        sa.Column("attempt", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint(
            "unit_kind IN ('file','device','table_row')", name="ck_processing_item_unit_kind"
        ),
        sa.CheckConstraint(
            "status IN ('pending','running','success','failed','skipped','cancelled')",
            name="ck_processing_item_status",
        ),
        sa.CheckConstraint("attempt >= 1", name="ck_processing_item_attempt"),
        sa.CheckConstraint(
            "(port IS NULL AND port_key = '') OR (port IS NOT NULL AND port_key = port)",
            name="ck_processing_item_port_key",
        ),
        sa.UniqueConstraint(
            "run_id", "unit_kind", "source_member", "port_key", name="uq_processing_item_input"
        ),
    )
    op.create_index("idx_processing_items_run_status", "processing_items", ["run_id", "status"])

    op.create_table(
        "quality_issues",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Text(),
            sa.ForeignKey("processing_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("item_id", sa.Text(), sa.ForeignKey("processing_items.id", ondelete="SET NULL")),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "severity IN ('error','warning','info')", name="ck_quality_issue_severity"
        ),
    )
    op.create_index("idx_quality_issues_run_severity", "quality_issues", ["run_id", "severity"])

    op.create_table(
        "staged_device_rows",
        sa.Column(
            "run_id",
            sa.Text(),
            sa.ForeignKey("processing_runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("source_device_key", sa.Text(), primary_key=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("payload_digest", sa.Text(), nullable=False),
    )
    op.create_index("idx_staged_device_rows_digest", "staged_device_rows", ["payload_digest"])

    op.create_table(
        "source_inputs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("batch_id", sa.BigInteger(), nullable=False),
        sa.Column("member_key", sa.Text(), nullable=False),
        sa.Column("port", sa.Text()),
        sa.Column("port_key", sa.Text(), server_default="", nullable=False),
        sa.Column("source_device_key", sa.Text(), nullable=False, unique=True),
        sa.Column("input_digest", sa.Text(), nullable=False),
        sa.Column("current_relpath", sa.Text()),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "(port IS NULL AND port_key = '') OR (port IS NOT NULL AND port_key = port)",
            name="ck_source_input_port_key",
        ),
        sa.UniqueConstraint("batch_id", "member_key", "port_key", name="uq_source_input_member"),
    )
    op.create_index("idx_source_inputs_batch", "source_inputs", ["batch_id"])
    op.create_index("idx_source_inputs_relpath", "source_inputs", ["batch_id", "current_relpath"])

    op.create_table(
        "cleanup_jobs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("batch_id", sa.Integer()),
        sa.Column("run_id", sa.Text()),
        sa.Column("resource_kind", sa.Text(), nullable=False),
        sa.Column("resource_path", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.Text()),
        sa.Column("error_code", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('pending','running','success','failed','cancelled')",
            name="ck_cleanup_job_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_cleanup_job_attempts"),
    )
    op.create_index("idx_cleanup_jobs_status_not_before", "cleanup_jobs", ["status", "not_before"])
    op.create_index("idx_cleanup_jobs_batch", "cleanup_jobs", ["batch_id"])

    op.create_table(
        "tombstones",
        sa.Column("origin_id", sa.Text(), primary_key=True),
        sa.Column("source_batch_id", sa.Text(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("deleted_revision", sa.Integer()),
        sa.Column("restored_revision", sa.Integer()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("restored_at", sa.DateTime(timezone=True)),
        sa.Column("purged_at", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("state IN ('active','deleted')", name="ck_tombstone_state"),
        sa.CheckConstraint("revision >= 0", name="ck_tombstone_revision"),
    )
    op.create_index("idx_tombstones_state_revision", "tombstones", ["state", "revision"])

    op.create_table(
        "local_replica_preferences",
        sa.Column("origin_id", sa.Text(), primary_key=True),
        sa.Column("source_batch_id", sa.Text(), primary_key=True),
        sa.Column("hidden", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.create_table(
        "upload_drafts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("group_id", sa.Text(), nullable=False),
        sa.Column("request_key", sa.Text(), nullable=False, unique=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("input_digest", sa.Text(), nullable=False),
        sa.Column("original_name", sa.Text(), nullable=False),
        sa.Column("upload_options", sa.JSON(), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "consumed_task_id",
            sa.BigInteger(),
            sa.ForeignKey("upload_tasks.id", ondelete="SET NULL"),
        ),
        sa.CheckConstraint(
            "status IN ('preparing','ready','failed','consumed','expired')",
            name="ck_upload_draft_status",
        ),
    )
    op.create_index("idx_upload_drafts_group", "upload_drafts", ["group_id"])
    op.create_index("idx_upload_drafts_status_expires", "upload_drafts", ["status", "expires_at"])

    op.create_table(
        "sync_operations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("origin_id", sa.Text(), nullable=False),
        sa.Column("source_batch_id", sa.Text(), nullable=False),
        sa.Column("source_device_key", sa.Text()),
        sa.Column("source_revision", sa.Integer()),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("request_key", sa.Text(), nullable=False, unique=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("receipt", sa.JSON()),
        sa.Column("confirmed_revision", sa.Integer()),
        sa.Column("error_code", sa.Text()),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("target IN ('server','web')", name="ck_sync_operation_target"),
        sa.CheckConstraint(
            "state IN ('local_only','pending','syncing','confirmed','conflict','failed','unknown')",
            name="ck_sync_operation_state",
        ),
        sa.CheckConstraint(
            "source_revision IS NULL OR source_revision >= 0", name="ck_sync_operation_revision"
        ),
    )
    op.create_index(
        "idx_sync_operations_source_target",
        "sync_operations",
        ["origin_id", "source_batch_id", "target"],
    )

    op.create_table(
        "export_jobs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("request_key", sa.Text(), nullable=False, unique=True),
        sa.Column("format", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("progress_pct", sa.SmallInteger(), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("query_spec", sa.JSON(), nullable=False),
        sa.Column("expected_sources", sa.JSON(), nullable=False),
        sa.Column("manifest", sa.JSON()),
        sa.Column("error", sa.JSON()),
        sa.Column("artifacts", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("format IN ('csv','xlsx')", name="ck_export_job_format"),
        sa.CheckConstraint(f"status IN ({_PROCESSING_STATUSES})", name="ck_export_job_status"),
        sa.CheckConstraint("progress_pct BETWEEN 0 AND 100", name="ck_export_job_progress"),
        sa.CheckConstraint("rows >= 0", name="ck_export_job_rows"),
    )
    op.create_index("idx_export_jobs_status_created", "export_jobs", ["status", "created_at"])
    op.create_index("idx_export_jobs_expires", "export_jobs", ["expires_at"])


def downgrade() -> None:
    raise RuntimeError(
        "platform schema v5 contains durable data and does not support automatic downgrade"
    )
