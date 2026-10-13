"""Receipt rules on two real databases, independent sessions and published web reads."""

import copy
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Device, SyncOperation
from app.services import sync_status as svc
from app.services.replica_service import export_batch
from app.services.source_identity import assign_batch_identity, batch_source_ref
from tests.fixtures.platform_db import DATABASES, platform_database


@pytest.fixture(params=DATABASES)
def source(request, tmp_path):
    with platform_database(request.param, tmp_path) as (db, batch):
        assign_batch_identity(db, batch)
        batch.device_count = 1
        db.add(
            Device(
                batch_id=batch.id,
                original_filename="one.s1p",
                source_device_key=str(uuid4()),
                qs=20,
                s_param_port="S11",
            )
        )
        db.commit()
        packet = export_batch(db, batch.id)
        ref = batch_source_ref(db, batch)
        db.rollback()
        yield db, batch, packet, ref


def test_confirm_restart_and_later_edit(source):
    db, batch, packet, ref = source
    job = svc.begin(db.bind, ref, "server", packet, request_key="receipt-1")
    assert svc.confirm_readback(db.bind, job, packet)
    with Session(db.bind) as restarted:
        assert (
            svc.statuses(restarted, restarted.get(type(batch), batch.id))["server"]["state"]
            == "confirmed"
        )
    db.scalar(select(Device)).qs = 21
    db.commit()
    status = svc.statuses(db, batch)["server"]
    assert status["state"] == "pending"
    assert status["confirmed_revision"] == ref["source_revision"]


@pytest.mark.parametrize("mutation", ["uuid", "revision", "value", "duplicate"])
def test_false_readback_never_confirmed(source, mutation):
    db, _, packet, ref = source
    job = svc.begin(db.bind, ref, "server", packet)
    bad = copy.deepcopy(packet)
    if mutation == "uuid":
        bad["source_ref"]["source_batch_id"] = str(uuid4())
    elif mutation == "revision":
        bad["source_ref"]["source_revision"] += 1
    elif mutation == "value":
        bad["devices"][0]["qs"] = 99
    else:
        bad["devices"].append(bad["devices"][0])
    assert not svc.confirm_readback(db.bind, job, bad)
    with Session(db.bind) as read:
        row = read.get(SyncOperation, job)
        assert row.state in {"unknown", "conflict"}
        assert row.receipt is None and row.last_success_at is None


def test_web_delayed_network_and_actual_publication(source, monkeypatch):
    db, batch, packet, ref = source
    from app.services import web_sync_service as web

    monkeypatch.setattr(web, "_request_json", lambda *a, **k: {"commit": {"html_url": "commit"}})
    answer = web.push_batch_to_web(db, batch.batch_no, "secret-session-token")
    assert answer["sync_state"] == "pending"
    monkeypatch.setattr(web, "fetch_web_snapshot", lambda: ([], [], []))
    svc.refresh_web(db, batch)
    assert svc.statuses(db, batch)["web"]["state"] == "pending"

    def disconnected():
        raise OSError("secret-session-token")

    monkeypatch.setattr(web, "fetch_web_snapshot", disconnected)
    svc.refresh_web(db, batch)
    assert svc.statuses(db, batch)["web"]["state"] == "unknown"
    published = {**packet["batch"], "source_revision": ref["source_revision"]}
    rows = [{**row, "batch_no": published["batch_no"]} for row in packet["devices"]]
    monkeypatch.setattr(web, "fetch_web_snapshot", lambda: ([published], [], rows))
    svc.refresh_web(db, batch)
    assert svc.statuses(db, batch)["web"]["state"] == "confirmed"
    operations = db.scalars(select(SyncOperation)).all()
    assert "secret-session-token" not in str([op.__dict__ for op in operations])


def test_conflict_no_rebase_and_old_synced_at_is_unknown(source):
    db, batch, packet, ref = source
    from datetime import UTC, datetime

    batch.synced_at = datetime.now(UTC)
    db.commit()
    assert svc.statuses(db, batch)["server"]["state"] == "unknown"
    db.rollback()
    job = svc.begin(db.bind, ref, "server", packet, request_key="original")
    svc.transition(db.bind, job, "conflict", "TARGET_CONFLICT")
    assert svc.begin(db.bind, ref, "server", packet, request_key="original") == job
    changed = {**ref, "source_revision": ref["source_revision"] + 1}
    with pytest.raises(Exception, match="SYNC_REQUEST_CONFLICT"):
        svc.begin(db.bind, changed, "server", packet, request_key="original")
    assert svc.statuses(db, batch)["server"]["state"] == "conflict"


def test_linked_table_readback_and_local_history(source, tmp_path):
    db, batch, packet, ref = source
    from app.models import Batch, SyncIdentity
    from app.services import replica_service as replication
    from app.services import replication_v3 as v3

    with platform_database("sqlite", tmp_path) as (desktop, table):
        table.batch_no, table.source, table.device_count = "metrics-table", "table", 1
        assign_batch_identity(desktop, table)
        desktop.add(
            Device(
                batch_id=table.id,
                original_filename="one.s1p",
                source_device_key=str(uuid4()),
                qs=35,
                s_param_port="S11",
            )
        )
        target = {"batch_no": batch.batch_no, "revision": ref["source_revision"], "source_ref": ref}
        import json

        desktop.add(SyncIdentity(key=f"table-target:{table.id}", value=json.dumps(target)))
        desktop.commit()
        table_id, original_uuid = table.id, table.source_batch_id

        class Client:
            def ensure_mapping(self, *_):
                return {"created": False}

            def request_json(self, method, path, **kwargs):
                db.rollback()
                if path.endswith("/catalog"):
                    return v3.catalog(db)
                if path.endswith("/resolve"):
                    return v3.resolve(db, ref["origin_id"], ref["source_batch_id"])
                if path.endswith("/tables"):
                    args = kwargs["json"]
                    return replication.accept_table(db, args["key"], args["payload"])
                value = export_batch(db, batch.id)
                value["protocol"] = 3
                return value

        result = replication.synchronize(desktop, Client(), protocol=3)
        assert result["errors"] == [] and result["tables_pushed"] == 1
        desktop.expire_all()
        retained = desktop.get(Batch, table_id)
        assert retained.deleted_at is not None and retained.source_batch_id == original_uuid
        assert desktop.scalar(select(Device).where(Device.batch_id == table_id)).qs == 35
        assert desktop.get(SyncIdentity, f"table-target:{table_id}").value == json.dumps(target)
        row = desktop.scalar(
            select(SyncOperation).where(SyncOperation.source_batch_id == original_uuid)
        )
        assert row.state == "confirmed"
        assert row.receipt["source_ref"]["source_batch_id"] == ref["source_batch_id"]
        assert row.expected["digest"] == row.receipt["digest"]
