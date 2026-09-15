"""端到端：子进程起真实服务器(SQLite + eager Celery)，桌面服务经 HTTP 推送/拉取。

标记 integration：默认跳过；手动运行：
    cd backend && uv run pytest tests/test_server_sync_e2e.py -v -m integration
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Base, Batch, Mapping, MappingEntry
from app.services import desktop_sync_service as svc
from app.services.server_client import ServerClient, ServerConfig

pytestmark = pytest.mark.integration

SERVER_PORT = 18123


@pytest.fixture(scope="module", autouse=True)
def _require_integration_flag(request: pytest.FixtureRequest) -> None:
    """默认跳过：必须显式 `pytest -m integration` 才会跑（同 test_real_pipeline 约定）。"""
    markexpr = request.config.getoption("-m") or ""
    if "integration" not in markexpr:
        pytest.skip("default-skipped; rerun with `-m integration` to enable")


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("aln-server")
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{root / 'server.db'}",
        "DATA_ROOT": str(root / "data"),
        "WATCH_ENABLED": "false",
        "CELERY_TASK_ALWAYS_EAGER": "1",
        "ALN_DESKTOP_MODE": "false",
        "DOTENV_PATH": str(root / "none.env"),  # 不加载仓库 .env
    }
    backend_dir = Path(__file__).resolve().parent.parent
    # alembic 全链在 sqlite 上跑不了（初始迁移用 JSONB）；桌面/测试建表走 create_all
    subprocess.run(
        [
            sys.executable, "-c",
            "from app.db import engine; from app.models import Base; Base.metadata.create_all(engine)",
        ],
        cwd=backend_dir, env=env, check=True, capture_output=True,
    )
    log_path = root / "server.log"
    with log_path.open("wb") as log_fh:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(SERVER_PORT)],
            cwd=backend_dir, env=env,
            stdout=log_fh, stderr=subprocess.STDOUT,
        )
        base = f"http://127.0.0.1:{SERVER_PORT}"
        try:
            for _ in range(120):
                if proc.poll() is not None:
                    tail = log_path.read_text(errors="replace")[-2000:]
                    raise RuntimeError(f"服务器子进程提前退出 (code={proc.returncode}):\n{tail}")
                try:
                    if httpx.get(f"{base}/api/health", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    time.sleep(0.5)
            else:
                tail = log_path.read_text(errors="replace")[-2000:]
                raise RuntimeError(f"服务器子进程启动超时:\n{tail}")
            yield base
        finally:
            proc.terminate()
            proc.wait(timeout=10)


def _desktop_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_push_then_pull_e2e(server, sample_zip, tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_files_root", lambda: tmp_path / "files")
    monkeypatch.setattr(svc, "_zip_staging_dir", lambda: tmp_path / "staging")
    monkeypatch.setattr(svc, "POLL_INTERVAL_S", 0.5)

    batch_no = "E2E-PUSH-1"
    # 模拟本地已完成 ingest 的批次：DB 行 + files 目录（来自样例 zip）
    db = _desktop_session()
    mapping = Mapping(name="E2E-MAP", file_path="m.csv", entry_count=1)
    db.add(mapping)
    db.flush()
    db.add(MappingEntry(mapping_id=mapping.id, mark="A1", has_pf=False))
    db.commit()
    files_dir = tmp_path / "files" / batch_no
    files_dir.mkdir(parents=True)
    with zipfile.ZipFile(sample_zip) as zf:
        zf.extractall(files_dir)
    batch = Batch(batch_no=batch_no, mapping_id=mapping.id, file_path="local.zip",
                  source="http", device_count=1)  # count 在服务器入库后校准
    db.add(batch)
    db.commit()

    client = ServerClient(ServerConfig(server_url=server, username="e2e"))
    try:
        # 推送（eager 服务器请求内完成入库）
        svc.ensure_mapping_on_server(db, client, batch)
        zip_path = svc.pack_batch_zip(batch, tmp_path / "staging" / f"{batch_no}.zip")
        result = client.push_batch(zip_path, {
            "batch_no": batch_no, "mapping_name": "E2E-MAP", "deembed": "false",
            "deembed_method": "default", "process_type": "AUTO", "uploaded_by": "e2e",
        })
        assert result["status"] in {"accepted", "already_synced"}
        if result["status"] == "accepted":
            svc.wait_task(client, int(result["task_id"]))
        # 用服务器真实计数校准后收尾（count 不一致保护路径由单测覆盖）
        batch.device_count = int(client.get_batch(batch_no)["device_count"])
        assert batch.device_count > 0
        db.commit()
        done = svc.finalize_synced_batch(db, client, batch_no)
        assert done["status"] == "synced"
        assert not files_dir.exists()
        assert db.scalar(select(Batch).where(Batch.batch_no == batch_no)).synced_at is not None

        # 第二台"新笔记本"拉快照
        other = _desktop_session()
        stats = svc.pull_from_server(other, client)
        assert stats["batches_added"] == 1
        pulled = other.scalar(select(Batch).where(Batch.batch_no == batch_no))
        assert pulled.source == "server"
    finally:
        client.close()
