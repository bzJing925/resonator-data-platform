"""桌面本地 worker 子进程化：CPU 密集任务放独立进程，避免占满 GIL 卡死 FastAPI。

注意：spawn 模式下子进程重新导入模块，monkeypatch 不会跨进程生效，
因此这里用 FakeProcess 同步执行 target，验证的是 worker loop 的派发/轮询/终止逻辑。
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from app.workers.local_queue import LocalTask


class _FakeProcess:
    """multiprocessing.Process 替身：后台线程同步跑 target，记录构造参数与 terminate。"""

    instances: list[_FakeProcess] = []

    def __init__(self, target=None, args=(), daemon=None):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.pid = 12345
        self.terminated = False
        self._alive = False
        self._thread: threading.Thread | None = None
        _FakeProcess.instances.append(self)

    def start(self) -> None:
        self._alive = True

        def run() -> None:
            try:
                self.target(*self.args)
            finally:
                self._alive = False

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def terminate(self) -> None:
        self.terminated = True


class _FakeQueue:
    """单任务队列：任务处理完（clear_cancel）后视为 shutdown，让 loop 退出。"""

    def __init__(self, task: LocalTask, *, cancel_after_start: bool = False):
        self._task = task
        self._given = False
        self._shutdown = False
        self._cancel_after_start = cancel_after_start
        self._cancel_checks = 0
        self.cleared: list[int] = []

    def get(self, timeout: float = 0.5):
        if not self._given:
            self._given = True
            return self._task
        return None

    def is_shutdown(self) -> bool:
        return self._shutdown

    def is_cancelled(self, task_id: int) -> bool:
        self._cancel_checks += 1
        if not self._cancel_after_start:
            return False
        # 第一次是启动前检查（False），之后轮询时返回 True 模拟执行中被取消
        return self._cancel_checks > 1

    def clear_cancel(self, task_id: int) -> None:
        self.cleared.append(task_id)
        self._shutdown = True


def test_task_subprocess_target_dispatches_by_kind(monkeypatch):
    """_task_subprocess_target 按 kind 分发到对应执行函数。"""
    from app.workers import local_worker

    calls = []
    monkeypatch.setattr(
        local_worker,
        "_run_upload_or_reextract",
        lambda t: calls.append(("upload", t.task_id)),
    )
    monkeypatch.setattr(
        local_worker, "_run_redeembed", lambda t: calls.append(("redeembed", t.task_id))
    )
    monkeypatch.setattr(
        local_worker, "_run_recompute", lambda t: calls.append(("recompute", t.task_id))
    )

    local_worker._task_subprocess_target(LocalTask(task_id=1, kind="upload"))
    local_worker._task_subprocess_target(LocalTask(task_id=2, kind="reextract"))
    local_worker._task_subprocess_target(LocalTask(task_id=3, kind="redeembed"))
    local_worker._task_subprocess_target(LocalTask(task_id=4, kind="recompute"))

    assert calls == [("upload", 1), ("upload", 2), ("redeembed", 3), ("recompute", 4)]


def test_worker_loop_runs_task_in_subprocess(monkeypatch):
    """worker loop 应为每个任务启动 daemon 子进程执行 _task_subprocess_target。"""
    from app.workers import local_worker

    task = LocalTask(task_id=7, kind="recompute", batch_no="B1", metrics=["qs"])
    queue = _FakeQueue(task)

    ran: list[int] = []
    monkeypatch.setattr(local_worker, "_run_recompute", lambda t: ran.append(t.task_id))
    monkeypatch.setattr(local_worker, "get_local_queue", lambda: queue)
    monkeypatch.setattr(local_worker, "multiprocessing", SimpleNamespace(Process=_FakeProcess))
    _FakeProcess.instances.clear()

    local_worker.local_worker_loop()

    assert len(_FakeProcess.instances) == 1
    proc = _FakeProcess.instances[0]
    assert proc.target is local_worker._task_subprocess_target
    assert proc.args == (task,)
    assert proc.daemon is True
    assert ran == [7]


def test_worker_loop_terminates_subprocess_on_cancel(monkeypatch):
    """轮询发现取消请求时应 terminate 子进程，并补写 cancelled 状态。"""
    from app.workers import local_worker

    started = threading.Event()
    release = threading.Event()

    def slow_runner(task: LocalTask) -> None:
        started.set()
        release.wait(5)

    task = LocalTask(task_id=9, kind="recompute", batch_no="B1", metrics=["qs"])
    queue = _FakeQueue(task, cancel_after_start=True)
    marked: list[int] = []

    monkeypatch.setattr(local_worker, "_run_recompute", slow_runner)
    monkeypatch.setattr(local_worker, "_mark_task_cancelled", lambda tid: marked.append(tid))
    monkeypatch.setattr(local_worker, "get_local_queue", lambda: queue)
    monkeypatch.setattr(local_worker, "multiprocessing", SimpleNamespace(Process=_FakeProcess))
    _FakeProcess.instances.clear()

    loop_thread = threading.Thread(target=local_worker.local_worker_loop, daemon=True)
    loop_thread.start()
    assert started.wait(5), "任务未开始执行"

    proc = _FakeProcess.instances[0]
    for _ in range(100):
        if proc.terminated:
            break
        time.sleep(0.05)
    release.set()
    loop_thread.join(5)

    assert not loop_thread.is_alive(), "worker loop 未在取消后退出"
    assert proc.terminated is True
    assert marked == [9]
