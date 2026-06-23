"""真链路端到端测试：真 Celery worker + 真 FastAPI server + 真 Redis pub/sub。

不同于 tests/api/test_integration.py（EAGER 模式，同步执行），本测试要求：
  1. 在另一个进程里跑 `uv run celery -A app.workers worker`
  2. 在另一个进程里跑 `uv run uvicorn app.main:app --port 8001`
  3. 然后通过 httpx 连过去做端到端验证，包括 SSE 流的真订阅。

跑法（pytest 默认 skip，必须显式 -m integration 才会跑）：
    uv run pytest -m integration tests/test_real_pipeline.py

也可独立直接跑：
    uv run python tests/test_real_pipeline.py
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

# 让独立运行（uv run python tests/test_real_pipeline.py）也能 import app.*
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import engine  # noqa: E402

API_BASE = "http://localhost:8001"
FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_MAPPING = FIXTURES / "mapping_ELB003.xlsx"
SAMPLE_ZIP = FIXTURES / "T8901P.01.zip"

# 业务上限（这个 zip 内有 12 个 DUT，S2P 模式下 24 行）
EXPECTED_MIN_DEVICES = 20
# SSE 至少要收到 5 条 progress 才算 worker 真在发心跳
MIN_PROGRESS_EVENTS = 5
# 整个任务最长等待
TASK_TIMEOUT_SEC = 180


def _truncate_tables() -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE devices, batches, upload_tasks, "
                "mapping_entries, mappings RESTART IDENTITY CASCADE"
            )
        )


def _upload_mapping(client: httpx.Client) -> int:
    with SAMPLE_MAPPING.open("rb") as f:
        r = client.post(
            "/api/mappings",
            files={"file": ("ELB003.xlsx", f)},
            data={"name": "ELB003"},
        )
    r.raise_for_status()
    return int(r.json()["id"])


def _upload_zip(client: httpx.Client, mapping_id: int) -> tuple[int, str]:
    with SAMPLE_ZIP.open("rb") as f:
        r = client.post(
            "/api/uploads",
            files={"file": ("T8901P.01.zip", f)},
            data={
                "mapping_id": str(mapping_id),
                "process_type": "S2P",
                "deembed": "false",
            },
        )
    if r.status_code != 202:
        raise RuntimeError(f"upload failed {r.status_code}: {r.text}")
    body = r.json()
    return int(body["task_id"]), body["stream_url"]


def _consume_sse(
    task_id: int,
    counters: dict[str, int],
    last_payload: dict[str, dict],
    stop_event: threading.Event,
) -> None:
    """同步打开 SSE 连接，把事件计数到 counters 里。"""
    url = f"{API_BASE}/api/tasks/{task_id}/stream"
    # trust_env=False 避免被 ALL_PROXY=socks5 干扰
    with httpx.Client(timeout=None, trust_env=False) as c:
        with c.stream("GET", url) as r:
            event_name: str | None = None
            data_buf: list[str] = []
            for line in r.iter_lines():
                if stop_event.is_set():
                    return
                # 流式 SSE：空行表示一条事件结束
                if line == "":
                    if event_name and data_buf:
                        raw = "\n".join(data_buf)
                        try:
                            payload = json.loads(raw)
                        except json.JSONDecodeError:
                            payload = {"_raw": raw}
                        counters[event_name] = counters.get(event_name, 0) + 1
                        last_payload[event_name] = payload
                        if event_name in ("done", "error"):
                            return
                    event_name = None
                    data_buf = []
                    continue
                if line.startswith("event:"):
                    event_name = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data_buf.append(line.split(":", 1)[1].lstrip())
                # 其他行（id:, retry:, 注释 :）忽略


def run_real_pipeline() -> dict:
    """主流程；返回结果汇总。失败抛异常。"""
    if not SAMPLE_MAPPING.exists():
        raise FileNotFoundError(SAMPLE_MAPPING)
    if not SAMPLE_ZIP.exists():
        raise FileNotFoundError(SAMPLE_ZIP)

    print("[step 1] truncate DB tables")
    _truncate_tables()

    print("[step 2] health check")
    with httpx.Client(base_url=API_BASE, timeout=30.0, trust_env=False) as client:
        r = client.get("/api/health")
        r.raise_for_status()
        health = r.json()
        print(f"  -> {health}")
        if health.get("redis") != "ok":
            raise RuntimeError(f"redis 不健康: {health}")

        print("[step 3] upload mapping")
        mapping_id = _upload_mapping(client)
        print(f"  -> mapping_id = {mapping_id}")

        print("[step 4] upload zip (triggers Celery task via real broker)")
        task_id, stream_url = _upload_zip(client, mapping_id)
        print(f"  -> task_id = {task_id}, stream_url = {stream_url}")

    # 5. 起 SSE 订阅线程
    counters: dict[str, int] = {}
    last_payload: dict[str, dict] = {}
    stop_event = threading.Event()
    sse_thread = threading.Thread(
        target=_consume_sse,
        args=(task_id, counters, last_payload, stop_event),
        daemon=True,
    )
    sse_thread.start()
    print("[step 5] SSE consumer thread started")

    # 6. 主线程 polling /api/tasks/{id} 看 status
    deadline = time.monotonic() + TASK_TIMEOUT_SEC
    final_status: str | None = None
    final_task_body: dict | None = None
    with httpx.Client(base_url=API_BASE, timeout=30.0, trust_env=False) as client:
        while time.monotonic() < deadline:
            r = client.get(f"/api/tasks/{task_id}")
            r.raise_for_status()
            body = r.json()
            print(
                f"  poll: status={body['status']} "
                f"pct={body['progress_pct']} msg={body['progress_msg']}"
            )
            if body["status"] in ("success", "failed"):
                final_status = body["status"]
                final_task_body = body
                break
            time.sleep(1.0)

    if final_status is None:
        stop_event.set()
        raise TimeoutError(f"task {task_id} 在 {TASK_TIMEOUT_SEC}s 内没有终态")

    # 等 SSE 线程把 done/error 事件吃完
    sse_thread.join(timeout=15.0)
    if sse_thread.is_alive():
        print("[warn] SSE thread 没在超时内退出，强制 stop")
        stop_event.set()

    print(f"[step 6] final task status = {final_status}")
    print(f"  task body: {final_task_body}")

    # 7. 验证 SSE 计数
    print(f"[step 7] SSE event counters: {counters}")
    progress_count = counters.get("progress", 0)
    done_count = counters.get("done", 0)
    error_count = counters.get("error", 0)

    assert progress_count >= MIN_PROGRESS_EVENTS, (
        f"progress 事件 {progress_count} < {MIN_PROGRESS_EVENTS}，"
        f"worker 可能没在发心跳"
    )
    assert error_count == 0, f"不应有 error 事件: {last_payload.get('error')}"
    assert done_count >= 1, f"必须收到至少 1 条 done 事件: {counters}"
    assert final_status == "success", (
        f"final status 应该是 success: {final_task_body}"
    )

    # 8. 验证 batch 入库情况
    print("[step 8] check batches & device_count")
    with httpx.Client(base_url=API_BASE, timeout=30.0, trust_env=False) as client:
        r = client.get("/api/batches")
        r.raise_for_status()
        batches = r.json()
        assert batches["total"] == 1
        device_count = batches["items"][0]["device_count"]
        print(f"  -> device_count = {device_count}")
        assert device_count >= EXPECTED_MIN_DEVICES, (
            f"device_count {device_count} < {EXPECTED_MIN_DEVICES}"
        )

    return {
        "task_id": task_id,
        "final_status": final_status,
        "progress_events": progress_count,
        "done_events": done_count,
        "error_events": error_count,
        "device_count": device_count,
        "done_payload": last_payload.get("done"),
    }


@pytest.mark.integration
def test_real_pipeline(request: pytest.FixtureRequest) -> None:
    """pytest 入口。

    默认 skip：必须显式 `pytest -m integration` 或独立脚本入口才会跑。
    依赖外部已起的 worker + uvicorn (port 8001)。
    """
    markexpr = request.config.getoption("-m") or ""
    if "integration" not in markexpr:
        pytest.skip("default-skipped; rerun with `-m integration` to enable")

    result = run_real_pipeline()
    assert result["progress_events"] >= MIN_PROGRESS_EVENTS
    assert result["done_events"] >= 1
    assert result["device_count"] >= EXPECTED_MIN_DEVICES


if __name__ == "__main__":
    try:
        result = run_real_pipeline()
    except Exception as exc:
        print(f"\nFAILED: {exc!r}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)

    print("\n=== SUMMARY ===")
    for k, v in result.items():
        print(f"  {k}: {v}")
    print("\nREAL PIPELINE OK")
