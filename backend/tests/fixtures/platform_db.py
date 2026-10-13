"""Isolated file SQLite / explicitly configured PostgreSQL test databases."""

import os
from contextlib import contextmanager
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.models import Base, Batch, Mapping
from app.services.replica_service import init_replication

DATABASES = ["sqlite", pytest.param("postgresql", marks=pytest.mark.integration)]


@contextmanager
def platform_database(kind, tmp_path):
    admin = None
    schema = f"aln_revision_test_{uuid4().hex[:12]}"
    if kind == "postgresql":
        url = os.environ.get("ALN_TEST_POSTGRES_URL")
        if not url:
            pytest.skip("ALN_TEST_POSTGRES_URL missing: PostgreSQL not verified")
        url = make_url(url)
        admin = create_engine(url)
        with admin.begin() as conn:
            conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        engine = create_engine(url.update_query_dict({"options": f"-csearch_path={schema}"}))
    else:
        engine = create_engine(f"sqlite:///{tmp_path / (uuid4().hex + '.sqlite')}")
        with engine.begin() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL")
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    try:
        Base.metadata.create_all(engine)
        init_replication(engine)
        with Session(engine) as db:
            mapping = Mapping(name="source-map", file_path="mapping.xlsx", entry_count=0)
            db.add(mapping)
            db.flush()
            batch = Batch(batch_no="source-lot", mapping_id=mapping.id, file_path="files")
            db.add(batch)
            db.commit()
            yield db, batch
    finally:
        engine.dispose()
        if admin is not None:
            with admin.begin() as conn:
                conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            admin.dispose()
