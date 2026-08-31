import threading
import time
from pathlib import Path

from app.workers.pipeline.watcher import FileWatcher


def test_watcher_discovers_new_files(tmp_path: Path) -> None:
    (tmp_path / "old.s1p").write_text("# old\n")

    watcher = FileWatcher(tmp_path, patterns=["*.s1p"], interval=0.05)
    stop = threading.Event()

    discovered: list[str] = []

    def consume() -> None:
        for p in watcher.watch(stop_event=stop):
            discovered.append(p.name)

    t = threading.Thread(target=consume)
    t.start()

    time.sleep(0.1)
    (tmp_path / "new.s1p").write_text("# new\n")
    time.sleep(0.15)
    stop.set()
    t.join(timeout=2)

    assert "new.s1p" in discovered
    assert "old.s1p" not in discovered
