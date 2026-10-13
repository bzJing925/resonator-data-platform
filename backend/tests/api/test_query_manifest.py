"""Real PG/SQLite snapshots report range, source and NULL count honestly."""
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import query
from app.models import Device
from app.schemas.query import AggregateRequest, MetricSpec, QueryRequest
from app.schemas.source import SourceRef
from app.services.replication_v3 import set_preference
from app.services.source_identity import assign_batch_identity
from tests.fixtures.platform_db import DATABASES, platform_database


@pytest.fixture(params=DATABASES)
def dataset(request, tmp_path, monkeypatch):
    with platform_database(request.param, tmp_path) as (db, batch):
        assign_batch_identity(db, batch)
        batch.result_revision = 1
        db.add_all([Device(batch_id=batch.id, original_filename=f'{i}.s1p',
                           source_device_key=str(uuid4()), qs=value) for i, value in
                   enumerate([1, 2, 3, None])])
        db.commit()
        monkeypatch.setattr(query, '_cache_get', lambda key: None)
        monkeypatch.setattr(query, '_cache_set', lambda *args, **kwargs: None)
        yield db, batch


def test_exact_unknown_sampled_and_source_rows(dataset, monkeypatch):
    db, batch = dataset
    result = query.query_devices(QueryRequest(fields=['batch_no'], limit=2, skip_count=True), db)
    assert result.total == 3 and result.manifest.matched_total is None
    assert result.manifest.total_relation == 'unknown' and result.manifest.truncated
    assert result.manifest.consistency == 'verified' and result.epoch is not None
    assert all(row['_source_ref']['source_device_key'] for row in result.rows)
    assert str(result.manifest.source_refs[0].source_batch_id) == batch.source_batch_id
    monkeypatch.setattr(query, '_SAMPLE_THRESHOLD', 2)
    result = query.query_devices(QueryRequest(fields=['id'], limit=10), db)
    assert result.manifest.matched_total == 4 and result.manifest.total_relation == 'exact'
    assert result.sampled and result.manifest.sampling.method == 'systematic'
    assert result.manifest.sampling.rate == result.sample_rate
    ids = [row['id'] for row in query.query_devices(
        QueryRequest(fields=['id'], sampling='none', order_by='batch_no'), db).rows]
    assert ids == sorted(ids)


def test_filters_empty_aggregate_null_and_distinct(dataset):
    db, _batch = dataset
    filters = {'op': 'or', 'children': [{'field': 'qs', 'op': 'lte', 'value': 1},
                                      {'field': 'qs', 'op': 'is_null'}]}
    result = query.query_devices(QueryRequest(filters=filters), db)
    assert result.manifest.matched_total == 2
    empty = query.query_devices(QueryRequest(filters={'qs': {'gt': 100}}), db)
    assert empty.total == 0 and empty.manifest.source_refs == []
    result = query.aggregate(AggregateRequest(group_by=['batch_no'],
        metrics=[MetricSpec(field='qs', agg=['count', 'avg'])]), db)
    assert result.groups[0]['qs'] == {'count': 3, 'avg': 2.0}
    assert result.manifest.matched_total == 4
    result = query.distinct_values(db, field='qs', limit=10)
    assert result['values'] == [1, 2, 3] and result['has_null']
    assert result['manifest']['matched_total'] == 3
    limited = query.distinct_values(db, field='qs', limit=1)
    assert limited['values'] == [1] and limited['has_null'] and limited['truncated']
    assert limited['manifest']['matched_total'] is None


def test_cache_and_hidden_scope_not_reused(dataset, monkeypatch):
    db, batch = dataset
    cache = {}
    monkeypatch.setattr(query, '_cache_get', cache.get)
    monkeypatch.setattr(query, '_cache_set',
                        lambda key, value, **kwargs: cache.update({key: value}))
    first = query.query_devices(QueryRequest(), db)
    with Session(db.bind) as writer:
        writer.scalar(select(Device).order_by(Device.id)).qs = 10
        writer.commit()
    second = query.query_devices(QueryRequest(), db)
    assert second.epoch > first.epoch
    assert (second.manifest.source_refs[0].source_revision
            > first.manifest.source_refs[0].source_revision)
    assert second.rows[0]['qs'] == 10
    # Preferences affect cache keys without changing the upstream revision.
    reference = SourceRef(origin_id=uuid4(), source_batch_id=uuid4(),
                          source_device_key=None, source_revision=None)
    db.rollback()
    set_preference(db.bind, reference, True)
    query.query_devices(QueryRequest(), db)
    assert len(cache) == 3


def test_changed_source_rejects_response(dataset, monkeypatch):
    db, _batch = dataset
    original = query.matching_sources
    def concurrent_change(source, where):
        result = original(source, where)
        with Session(db.bind) as writer:
            writer.scalar(select(Device).order_by(Device.id)).qs = 100
            writer.commit()
        return result
    monkeypatch.setattr(query, 'matching_sources', concurrent_change)
    with pytest.raises(HTTPException) as exc:
        query.query_devices(QueryRequest(), db)
    assert exc.value.status_code == 409
    assert exc.value.detail['code'] == 'QUERY_SOURCE_CHANGED'


def test_legacy_publication_is_unverified(dataset):
    db, batch = dataset
    batch.result_revision = 0
    db.commit()
    assert query.query_devices(QueryRequest(), db).manifest.consistency == 'unverified'
