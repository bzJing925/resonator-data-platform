"""Complete filtered SELECTs: one read-only snapshot, no graph limits or sampling."""
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models import Batch, Device
from app.services.batch_scope import active_batch_clause
from app.services.query_manifest import matching_sources, scope_version
from app.services.query_spec import _build_filters, _resolve_column, query_spec, stable_order


class ExportWriteError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass
class QuerySnapshot:
    db: Session
    fields: list
    statement: object
    total: int
    sources: dict
    verified: bool
    epoch: int
    scope_digest: str

    def partitions(self):
        result = self.db.execute(self.statement.execution_options(yield_per=2000)).mappings()
        try:
            yield from result.partitions(2000)
        finally:
            result.close()


@contextmanager
def export_snapshot(engine, ticket):
    spec = query_spec(**ticket['query_spec'])
    with engine.connect() as conn:
        if engine.dialect.name == 'postgresql':
            conn = conn.execution_options(isolation_level='REPEATABLE READ')
        with Session(conn) as db:
            db.execute(text('SET TRANSACTION READ ONLY' if engine.dialect.name == 'postgresql'
                            else 'BEGIN'))
            version, scope_digest = scope_version(db)
            if (version is None or version[1] != ticket['source_epoch']
                    or scope_digest != ticket['scope_digest']):
                raise ExportWriteError('SOURCE_CHANGED')
            where = [active_batch_clause(), *_build_filters(spec.filters)]
            sources, verified = matching_sources(db, where)
            actual = sorted((ref for ref in sources.values() if ref),
                            key=lambda ref: (ref['origin_id'], ref['source_batch_id']))
            expected = sorted(ticket['expected_sources'],
                              key=lambda ref: (ref['origin_id'], ref['source_batch_id']))
            if actual != expected:
                raise ExportWriteError('SOURCE_CHANGED')
            total = db.scalar(select(func.count()).select_from(Device)
                .join(Batch, Device.batch_id == Batch.id).where(*where)) or 0
            stmt = select(*[_resolve_column(f).label(f) for f in spec.fields]).select_from(Device)
            stmt = stmt.join(Batch, Device.batch_id == Batch.id).where(*where)
            stmt = stmt.order_by(*stable_order(spec.order_by))
            yield QuerySnapshot(db, spec.fields, stmt, int(total), sources, verified,
                                version[1], scope_digest)
