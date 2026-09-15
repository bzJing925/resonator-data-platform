"""CELERY_TASK_ALWAYS_EAGER=1 时任务内联执行（供无 Redis 的端到端测试）。"""

import os
import subprocess
import sys


def test_celery_eager_via_env() -> None:
    code = (
        "from app.workers.celery_app import celery_app; "
        "print(celery_app.conf.task_always_eager)"
    )
    env = {**os.environ, "CELERY_TASK_ALWAYS_EAGER": "1"}
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True
    )
    assert out.stdout.strip() == "True"
