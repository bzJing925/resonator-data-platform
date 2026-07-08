"""桌面模式下的本地 SQLite 任务队列。"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)


@dataclass
class LocalTask:
    task_id: int
    zip_path: Path | None = None
    batch_no: str | None = None
    mapping_id: int | None = None
    f_start_ghz: float | None = None
    f_end_ghz: float | None = None
    deembed: bool = False
    deembed_method: str = "default"
    process_type: str = "AUTO"
    kind: Literal["upload", "reextract", "redeembed", "recompute"] = "upload"
    metrics: list[str] | None = None


class LocalTaskQueue:
    def __init__(self) -> None:
        self._pending: deque[LocalTask] = deque()
        self._cancelled_ids: set[int] = set()
        self._lock = threading.Lock()
        self._shutdown = threading.Event()
        self._worker: threading.Thread | None = None
        self._event = threading.Event()

    def put(self, task: LocalTask) -> None:
        with self._lock:
            self._pending.append(task)
        self._event.set()

    def get(self, timeout: float = 0.5) -> LocalTask | None:
        if self._event.wait(timeout):
            with self._lock:
                if self._pending:
                    item = self._pending.popleft()
                    if not self._pending:
                        self._event.clear()
                    return item
            self._event.clear()
        return None

    def list_pending(self) -> list[LocalTask]:
        with self._lock:
            return list(self._pending)

    def request_cancel(self, task_id: int) -> bool:
        """请求取消任务。若任务仍在待处理队列中则移除，并标记为已请求取消。"""
        with self._lock:
            removed = False
            for i, task in enumerate(self._pending):
                if task.task_id == task_id:
                    del self._pending[i]
                    removed = True
                    break
            self._cancelled_ids.add(task_id)
            return removed

    def is_cancelled(self, task_id: int) -> bool:
        with self._lock:
            return task_id in self._cancelled_ids

    def clear_cancel(self, task_id: int) -> None:
        with self._lock:
            self._cancelled_ids.discard(task_id)

    def shutdown(self) -> None:
        self._shutdown.set()
        self._event.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=5.0)

    def is_shutdown(self) -> bool:
        return self._shutdown.is_set()


_local_queue = LocalTaskQueue()


def get_local_queue() -> LocalTaskQueue:
    return _local_queue


def start_local_worker() -> threading.Thread:
    from app.workers.local_worker import local_worker_loop

    t = threading.Thread(target=local_worker_loop, name="aln-local-worker", daemon=True)
    _local_queue._worker = t
    t.start()
    return t


def stop_local_worker() -> None:
    _local_queue.shutdown()
