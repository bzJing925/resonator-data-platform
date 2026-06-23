"""Filter 树健壮性测试。

覆盖 _build_node 深度上限、非法节点形状、空组等边界，
确保恶意/手写 JSON 不会让 API 抛 500（RecursionError 或其他未捕获异常）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def _nested_or(depth: int) -> dict:
    """构造 depth 层 OR 嵌套 + 一个叶。
    depth=0 → 叶节点；depth=1 → {op:or, children:[叶]}；……
    """
    node: dict = {"field": "qs", "op": "gt", "value": 0}
    for _ in range(depth):
        node = {"op": "or", "children": [node]}
    return node


def test_filter_tree_within_depth_succeeds(client: TestClient) -> None:
    """30 层嵌套（< 32 上限）应当被接受，不报 500 也不报 400。"""
    payload = {"limit": 1, "filters": _nested_or(30)}
    r = client.post("/api/query/devices", json=payload)
    # 真正的数据不存在不要紧——这里只验证查询不被 reject、不爆 500
    assert r.status_code == 200, r.text


def test_filter_tree_exceeding_depth_returns_400(client: TestClient) -> None:
    """100 层嵌套远超上限，应返回 400 而不是 500（RecursionError）。"""
    payload = {"limit": 1, "filters": _nested_or(100)}
    r = client.post("/api/query/devices", json=payload)
    assert r.status_code == 400, r.text
    assert "嵌套" in r.json()["detail"]


def test_filter_node_must_be_dict(client: TestClient) -> None:
    payload = {"limit": 1, "filters": {"op": "and", "children": ["not_a_dict"]}}
    r = client.post("/api/query/devices", json=payload)
    assert r.status_code == 400, r.text


def test_filter_children_must_be_list(client: TestClient) -> None:
    payload = {"limit": 1, "filters": {"op": "and", "children": "oops"}}
    r = client.post("/api/query/devices", json=payload)
    assert r.status_code == 400, r.text


def test_filter_empty_group_accepted(client: TestClient) -> None:
    """空 children 视为恒真，方便 UI 还没配条件时就能发请求。"""
    payload = {"limit": 1, "filters": {"op": "and", "children": []}}
    r = client.post("/api/query/devices", json=payload)
    assert r.status_code == 200, r.text


def test_query_devices_works_with_only_batch_no_field(client: TestClient) -> None:
    """fields 只选 batch_no（Batch 表字段）也不能 500。

    历史 bug：`select(Batch.batch_no).join(Batch, ...)` SQLAlchemy 没法从
    select_cols 推断左侧表（没 Device 列），抛 InvalidRequestError 500。
    必须显式 .select_from(Device)。
    """
    r = client.post(
        "/api/query/devices",
        json={"filters": {}, "fields": ["batch_no"], "limit": 5},
    )
    assert r.status_code == 200, r.text


def test_query_aggregate_works_with_only_batch_no_group_by(client: TestClient) -> None:
    """group_by 只含 batch_no 同样不能 500。"""
    r = client.post(
        "/api/query/aggregate",
        json={
            "filters": {},
            "group_by": ["batch_no"],
            "metrics": [{"field": "qs", "agg": ["avg"]}],
        },
    )
    assert r.status_code == 200, r.text


def test_query_devices_order_by_batch_no(client: TestClient) -> None:
    """order_by 走 Batch 表字段也不能 500。"""
    r = client.post(
        "/api/query/devices",
        json={
            "filters": {},
            "fields": ["id", "batch_no", "fs_ghz"],
            "order_by": "batch_no",
            "limit": 5,
        },
    )
    assert r.status_code == 200, r.text


def test_query_devices_order_by_batch_no_desc(client: TestClient) -> None:
    r = client.post(
        "/api/query/devices",
        json={
            "filters": {},
            "fields": ["id", "batch_no"],
            "order_by": "-batch_no",
            "limit": 5,
        },
    )
    assert r.status_code == 200, r.text


def test_query_aggregate_with_batch_no_in_filter_tree(client: TestClient) -> None:
    """AND/OR 树过滤器引用 batch_no 时，count + select 两条 stmt 都要正确 join Batch。"""
    payload = {
        "filters": {
            "op": "or",
            "children": [
                {"field": "batch_no", "op": "eq", "value": "no_such_batch"},
                {"field": "qs", "op": "gt", "value": 0},
            ],
        },
        "group_by": ["batch_no"],
        "metrics": [{"field": "qs", "agg": ["count", "avg"]}],
    }
    r = client.post("/api/query/aggregate", json=payload)
    assert r.status_code == 200, r.text


def test_query_devices_unknown_order_by_rejected(client: TestClient) -> None:
    """未知 order_by 字段必须 400，防 SQL 注入到 ORDER BY。"""
    r = client.post(
        "/api/query/devices",
        json={"filters": {}, "fields": ["id"], "order_by": "DROP TABLE"},
    )
    assert r.status_code == 400


def test_query_aggregate_unknown_metric_field_rejected(client: TestClient) -> None:
    """metric field 不在白名单 → 400。"""
    r = client.post(
        "/api/query/aggregate",
        json={
            "filters": {},
            "group_by": ["batch_no"],
            "metrics": [{"field": "DROP", "agg": ["avg"]}],
        },
    )
    assert r.status_code == 400


def test_query_aggregate_unknown_agg_op_rejected(client: TestClient) -> None:
    """聚合操作符不在白名单 → 必须 4xx，不能 500（schema 层 422 或应用层 400 都行）。"""
    r = client.post(
        "/api/query/aggregate",
        json={
            "filters": {},
            "group_by": ["batch_no"],
            "metrics": [{"field": "qs", "agg": ["evil"]}],
        },
    )
    assert 400 <= r.status_code < 500, r.text


def test_query_distinct_field_whitelisted(client: TestClient) -> None:
    """distinct field 必须经白名单 — 否则 SQL 注入到 SELECT DISTINCT。"""
    r = client.get("/api/query/distinct", params={"field": "DROP TABLE"})
    assert r.status_code == 400


def test_query_distinct_batch_no_works(client: TestClient) -> None:
    """distinct=batch_no 应当通过特殊分支走 Batch.batch_no。"""
    r = client.get("/api/query/distinct", params={"field": "batch_no", "limit": 10})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["field"] == "batch_no"
    assert "values" in body and isinstance(body["values"], list)


def test_mapping_name_rejects_path_chars(client: TestClient) -> None:
    """对照表 name 直接拼进磁盘文件路径，必须挡掉路径字符。

    历史风险：name=../../etc/foo 时 saved_path 会逃出 mappings_dir。
    """
    import io

    bad_names = ["../etc/evil", "foo/bar", "foo\\bar", "..", "foo..bar"]
    for bn in bad_names:
        r = client.post(
            "/api/mappings",
            files={"file": ("x.xlsx", io.BytesIO(b"fake"))},
            data={"name": bn},
        )
        assert r.status_code == 400, f"name={bn!r} 应被拒绝，实际 {r.status_code}: {r.text}"
        assert "name" in r.json()["detail"]
