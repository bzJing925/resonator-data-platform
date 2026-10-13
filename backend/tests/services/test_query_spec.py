"""API and exports share validated, stable query specifications."""
import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import sqlite

from app.schemas.query import QueryRequest
from app.services.export_query import build_export_fields_and_stmt
from app.services.query_spec import _build_filters, query_spec, stable_order


def test_legacy_and_tree_normalize_equally():
    old = {'qs': {'gte': 1}, 'batch_no': ['lot']}
    tree = {'op': 'and', 'children': [{'field': 'qs', 'op': 'gte', 'value': 1},
                                    {'field': 'batch_no', 'op': 'in', 'value': ['lot']}]}
    assert query_spec(old, ['batch_no']).filters == query_spec(tree, ['batch_no']).filters
    assert len(_build_filters({'op': 'or', 'children': tree['children']})) == 1


@pytest.mark.parametrize('fields,order,filters', [(['injected'], None, {}),
    (['id'], 'injected', {}), (['id'], None, {'qs': {}}),
    (['id'], None, {'injected': 1}), (['id'], None, {'qs': {'in': []}})])
def test_invalid_rules_are_rejected(fields, order, filters):
    with pytest.raises(HTTPException):
        query_spec(filters, fields, order)


def test_export_batch_only_and_stable_ties():
    fields, stmt = build_export_fields_and_stmt(QueryRequest(fields=['batch_no'], order_by='qs'))
    sql = str(stmt.compile(dialect=sqlite.dialect()))
    assert fields == ['batch_no']
    assert 'FROM devices JOIN batches' in sql and 'ORDER BY devices.qs ASC, devices.id ASC' in sql
    assert len(stable_order(None)) == 1
