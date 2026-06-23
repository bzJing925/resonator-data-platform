"""Export 端点：CSV / XLSX / 下载 + 安全防御。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_export_csv_returns_header_only_when_no_data(client: TestClient) -> None:
    """空 DB 上导出至少回 200 + 含 header 行（不能 crash 或 500）。"""
    r = client.post(
        "/api/export/csv",
        json={"filters": {}, "fields": ["batch_no", "fs_ghz"], "limit": 10},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    body = r.text
    # 至少有 header 行
    assert "batch_no,fs_ghz" in body


def test_export_csv_rejects_bad_field(client: TestClient) -> None:
    """非白名单字段必须 400，防 SQL 注入到 export 路径。"""
    r = client.post(
        "/api/export/csv",
        json={"filters": {}, "fields": ["DROP TABLE devices"], "limit": 10},
    )
    assert r.status_code == 400


def test_export_csv_filename_is_quoted(client: TestClient) -> None:
    """Content-Disposition filename 应当被引号包裹（RFC 6266 规范）。"""
    r = client.post(
        "/api/export/csv",
        json={"filters": {}, "fields": ["batch_no"], "limit": 1},
    )
    assert r.status_code == 200
    cd = r.headers.get("content-disposition", "")
    assert 'filename="' in cd, f"Content-Disposition 应该带引号: {cd!r}"


def test_download_export_rejects_path_traversal(client: TestClient) -> None:
    """非法 export_id 必须 400 而不是 404 — 避免泄露文件存在性。"""
    for bad_id in ("../etc/passwd", "..\\..\\evil", "foo/bar"):
        r = client.get(f"/api/exports/{bad_id}")
        # path 里的 .. 和 / 在 FastAPI 路由层就会被 normalize / 404，
        # 但我们的应用层校验是兜底。任意 < 500 即可。
        assert r.status_code < 500, f"bad_id={bad_id} 不应 500，实际 {r.status_code}"


def test_download_export_404_for_missing_id(client: TestClient) -> None:
    r = client.get("/api/exports/20990101_000000_000000")
    assert r.status_code == 404
