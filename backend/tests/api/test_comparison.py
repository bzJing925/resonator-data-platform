"""Source comparison HTTP contracts use real isolated databases."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.services.test_comparison_service import cohorts, request_body  # noqa: F401


def test_compare_http_and_stale_source(cohorts):  # noqa: F811
    from app.db import get_db
    from app.main import app

    db, a, b = cohorts
    previous = dict(app.dependency_overrides)

    def sessions():
        with Session(db.bind) as source:
            yield source

    app.dependency_overrides[get_db] = sessions
    try:
        client = TestClient(app)
        body = request_body(db, a, b)
        response = client.post("/api/analysis/compare", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["cohorts"]["a"]["statistics"]["qs"]["mean"] == 2
        body["comparison"]["a"]["source"]["source_revision"] = 0
        response = client.post("/api/analysis/compare", json=body)
        assert response.status_code == 409, response.text
        body["comparison"]["scope"] = "workspace_snapshot"
        assert client.post("/api/analysis/compare", json=body).status_code == 422
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def test_query_metadata_declares_canonical_units():
    from app.api.query import fields_metadata
    from app.services.field_metadata import NUMERIC_UNITS

    units = {field["name"]: field["unit"] for field in fields_metadata()["numeric"]}
    assert units["fs_ghz"] == "GHz" and units["qs"] == "1" and units["k2eff_pct"] == "%"
    assert units["zs_ohm"] == NUMERIC_UNITS["zs_ohm"]


def test_conditions_http_is_read_only_and_version_bound(cohorts):  # noqa: F811
    from app.db import get_db
    from app.main import app

    db, a, b = cohorts
    previous = dict(app.dependency_overrides)

    def sessions():
        with Session(db.bind) as source:
            yield source

    app.dependency_overrides[get_db] = sessions
    try:
        client = TestClient(app)
        body = request_body(db, a, b)
        response = client.post("/api/analysis/compare/conditions", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["endpoints"]["a"]["matched_total"] == 4
        assert "statistics" not in response.json()["endpoints"]["a"]
        body["comparison"]["b"]["source"]["source_revision"] = 0
        assert client.post("/api/analysis/compare/conditions", json=body).status_code == 409
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
