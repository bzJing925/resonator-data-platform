from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)


class FileWatcher:
    """轮询目录，产出匹配模式的新文件。"""

    def __init__(self, root_dir: str | Path, patterns: list[str], interval: float = 1.0):
        self.root_dir = Path(root_dir)
        self.patterns = patterns
        self.interval = interval
        self._seen: set[str] = set()
        # 将构造时已存在的文件标记为已见，不产出它们
        self._scan()

    def _scan(self) -> list[Path]:
        found: list[Path] = []
        for pattern in self.patterns:
            for p in self.root_dir.rglob(pattern):
                if p.is_file():
                    relpath = str(p.relative_to(self.root_dir))
                    if relpath not in self._seen:
                        self._seen.add(relpath)
                        found.append(p)
        return found

    def watch(self, stop_event: threading.Event) -> Iterator[Path]:
        """持续轮询直到 stop_event 被设置；结束时产出剩余新文件。"""
        while not stop_event.is_set():
            for p in self._scan():
                yield p
            time.sleep(self.interval)
        # 最终扫描
        for p in self._scan():
            yield p
