"""bulk_upload 脚本的简单单元测试：用 httpx MockTransport，无需真服务器。"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import bulk_upload  # noqa: E402


def make_zip(dir_: Path, name: str) -> Path:
    p = dir_ / f"{name}.zip"
    p.write_bytes(b"PK\x03\x04fake-zip")
    return p


def test_dry_run_lists_zips_without_requests(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    zip_dir = tmp_path / "zips"
    zip_dir.mkdir()
    make_zip(zip_dir, "T8901P.01")
    make_zip(zip_dir, "T8902")

    rc = bulk_upload.main(
        [
            "--base-url",
            "http://localhost:8000",
            "--zip-dir",
            str(zip_dir),
            "--mapping-id",
            "1",
            "--dry-run",
        ]
    )
    out = capsys.readouterr().out
    assert rc == bulk_upload.EXIT_OK
    assert "T8901P.01" in out
    assert "T8902" in out
    assert "dry-run" in out


def test_dry_run_empty_dir(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    zip_dir = tmp_path / "empty"
    zip_dir.mkdir()
    rc = bulk_upload.main(
        [
            "--base-url",
            "http://localhost:8000",
            "--zip-dir",
            str(zip_dir),
            "--mapping-id",
            "1",
            "--dry-run",
        ]
    )
    out = capsys.readouterr().out
    assert rc == bulk_upload.EXIT_OK
    assert "未发现 zip 文件" in out


def test_skip_existing_via_mock_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    zip_dir = tmp_path / "zips"
    zip_dir.mkdir()
    make_zip(zip_dir, "T8901P.01")  # already exists on server
    make_zip(zip_dir, "T9999")  # new

    upload_calls: list[str] = []
    task_state = {"polled": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = request.url
        path = url.path
        if request.method == "GET" and path == "/api/batches":
            return httpx.Response(
                200,
                json={
                    "total": 1,
                    "page": 1,
                    "size": 200,
                    "items": [{"batch_no": "T8901P.01"}],
                },
            )
        if request.method == "POST" and path == "/api/uploads":
            # Read filename from the multipart body (rough check).
            body = request.content.decode("latin-1", errors="ignore")
            for stem in ("T8901P.01", "T9999"):
                if f"{stem}.zip" in body:
                    upload_calls.append(stem)
                    return httpx.Response(
                        202,
                        json={
                            "task_id": "42",
                            "batch_no": stem,
                            "status": "pending",
                            "stream_url": "/api/tasks/42/stream",
                        },
                    )
            return httpx.Response(400, json={"detail": "no file"})
        if request.method == "GET" and path.startswith("/api/tasks/"):
            task_state["polled"] += 1
            return httpx.Response(
                200,
                json={
                    "id": 42,
                    "batch_no": "T9999",
                    "status": "success",
                    "progress_pct": 100,
                    "progress_msg": "ok",
                    "started_at": "2026-01-01T00:00:00Z",
                    "finished_at": "2026-01-01T00:01:00Z",
                    "error_msg": None,
                },
            )
        if request.method == "GET" and path.startswith("/api/batches/"):
            return httpx.Response(200, json={"device_count": 1234})
        return httpx.Response(404, json={"detail": f"unhandled {request.method} {path}"})

    real_client_cls = httpx.Client
    transport = httpx.MockTransport(handler)

    def fake_client(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = transport
        return real_client_cls(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(bulk_upload.httpx, "Client", fake_client)
    monkeypatch.setattr(bulk_upload, "POLL_INTERVAL_SEC", 0.0)

    rc = bulk_upload.main(
        [
            "--base-url",
            "http://localhost:8000",
            "--zip-dir",
            str(zip_dir),
            "--mapping-id",
            "1",
            "--skip-existing",
        ]
    )

    out = capsys.readouterr().out
    assert rc == bulk_upload.EXIT_OK
    assert upload_calls == ["T9999"], f"only T9999 should upload, got {upload_calls}"
    assert "skip: batch already exists" in out
    assert "成功 1" in out
    assert "跳过 1" in out
