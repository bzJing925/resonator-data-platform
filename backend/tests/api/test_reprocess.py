from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_recompute_rejects_missing_batch():
    resp = client.post("/api/batches/NOT_EXISTS/recompute", json={"metrics": ["qs"]})
    assert resp.status_code == 404
