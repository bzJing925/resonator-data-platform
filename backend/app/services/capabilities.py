"""Capabilities describe initialized services and an authenticated source handshake."""

from sqlalchemy import inspect

from app.config import get_settings
from app.models import SyncIdentity
from app.schemas.capabilities import Capabilities, CapabilityFeatures


def executor_ready(database):
    settings = get_settings()
    if settings.is_desktop:
        from app.workers.local_queue import get_local_queue

        queue = get_local_queue()
        return bool(queue._worker and queue._worker.is_alive() and not queue.is_shutdown())
    try:
        from app.workers.celery_app import celery_app

        return bool(celery_app.control.inspect(timeout=0.5).ping())
    except Exception:
        return False


def upstream_delete_ready():
    from app.services.replication_v3 import validate_catalog
    from app.services.server_client import ServerClient, load_config

    config = load_config()
    if config is None:
        return False
    try:
        with ServerClient(config) as client:
            caps = Capabilities.model_validate(client.request_json("GET", "/capabilities"))
            catalog = client.request_json("GET", "/sync/v3/catalog")
            validate_catalog(catalog)
            return (
                caps.mode == "server"
                and "sync-v3" in caps.protocols
                and caps.features.remote_delete
            )
    except Exception:
        return False


def describe(db):
    settings = get_settings()
    database = db.bind.dialect.name
    tables = set(inspect(db.bind).get_table_names())
    initialized = {
        "processing_runs",
        "export_jobs",
        "source_inputs",
        "sync_operations",
        "data_epoch",
        "tombstones",
    } <= tables and db.get(SyncIdentity, "server") is not None
    mode = "server" if settings.ALN_SERVER_MODE or not settings.is_desktop else "desktop"
    executor = initialized and executor_ready(database)
    remote_delete = bool(initialized and (mode == "server" or upstream_delete_ready()))
    return Capabilities(
        schema_version=1,
        mode=mode,
        database=database,
        protocols=["sync-v3"] if initialized else [],
        features=CapabilityFeatures(
            upload_precheck=executor,
            processing_reports=bool(initialized),
            projects=True,
            full_export_csv=executor,
            full_export_xlsx=executor,
            batch_compare=bool(initialized),
            trash=bool(initialized),
            replication_v3=bool(initialized),
            remote_delete=remote_delete,
            credential_storage=False,
        ),
        limits={
            "full_export_max_rows": settings.EXPORT_MAX_ROWS,
            "full_export_max_bytes": settings.EXPORT_MAX_BYTES,
            "comparison_max_rows": settings.COMPARISON_MAX_ROWS,
        },
    )
