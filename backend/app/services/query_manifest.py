"""Read one database snapshot and reject replies whose source changed meanwhile."""
from datetime import UTC, datetime
from functools import wraps
from hashlib import sha256
from inspect import signature
from json import dumps
from typing import get_type_hints

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Batch, Device, LocalReplicaPreference
from app.schemas.result_manifest import ResultManifest, SamplingSpec
from app.services.api_errors import api_error
from app.services.data_revision import cache_version
from app.services.replication_v3 import source_snapshot
from app.services.source_identity import batch_source_ref


def scope_version(db):
    preferences = db.execute(select(LocalReplicaPreference.origin_id,
        LocalReplicaPreference.source_batch_id, LocalReplicaPreference.hidden)
        .order_by(LocalReplicaPreference.origin_id, LocalReplicaPreference.source_batch_id)).all()
    return cache_version(db), sha256(dumps([list(row) for row in preferences]).encode()).hexdigest()


def snapshot_query(function):
    sig = signature(function)
    hints = get_type_hints(function, include_extras=True)

    @wraps(function)
    def wrapped(*args, **kwargs):
        bound = sig.bind(*args, **kwargs)
        engine = bound.arguments['db'].bind
        with source_snapshot(engine) as source:
            before = scope_version(source)
            bound.arguments['db'] = source
            result = function(*bound.args, **bound.kwargs)
        with Session(engine) as current:
            if scope_version(current) != before:
                raise api_error(409, 'QUERY_SOURCE_CHANGED', '查询期间来源变化，请重新查询')
        return result

    wrapped.__signature__ = sig.replace(parameters=[
        p.replace(annotation=hints.get(name, p.annotation)) for name, p in sig.parameters.items()
    ], return_annotation=hints.get('return', sig.return_annotation))
    return wrapped


def matching_sources(db, where):
    batches = db.scalars(select(Batch).where(Batch.id.in_(
        select(Device.batch_id).join(Batch, Device.batch_id == Batch.id).where(*where)
    )).order_by(Batch.id)).all()
    references = {}
    verified = cache_version(db) is not None
    for batch in batches:
        ref = batch_source_ref(db, batch)
        references[batch.id] = ref
        verified = verified and ref is not None and ref['source_revision'] is not None
        # A legacy result has no publication evidence; its UUID alone isn't proof.
        verified = verified and batch.result_revision > 0
    return references, bool(verified)


def make_manifest(spec, sources, verified, total, returned, truncated=False, rate=None):
    return ResultManifest(schema_version=1, query_spec=spec,
        source_refs=[ref for ref in sources.values() if ref is not None],
        matched_total=total, total_relation='unknown' if total is None else 'exact',
        returned=returned, truncated=truncated,
        sampling=SamplingSpec(method='systematic' if rate is not None else 'none', rate=rate),
        local_edits=0, excluded_rows=0, generated_at=datetime.now(UTC),
        consistency='verified' if verified else 'unverified')
