"""桌面模式下的本地 SQLite 任务队列。"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class LocalTask:
    task_id: int
    zip_path: Path
    batch_no: str
    mapping_id: int
    f_start_ghz: float | None
    f_end_ghz: float | None
    deembed: bool
    deembed_method: str
    process_type: str


class LocalTaskQueue:
    def __init__(self) -> None:
        self._pending: deque[LocalTask] = deque()
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
