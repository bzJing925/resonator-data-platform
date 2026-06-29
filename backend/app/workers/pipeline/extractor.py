from __future__ import annotations

import logging
import shutil
import subprocess
import time
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Literal

from app.core.filename import parse_filename

logger = logging.getLogger(__name__)


def zip_contains_calibration(
    zip_path: str | Path,
    method: Literal["default", "basic"] = "default",
) -> bool:
    """检查 zip 内是否包含 OPEN/SHORT 校准件 .s2p。

    使用 filename.parse_filename 统一识别，避免与 extract_batch 的识别方式不一致。
    - default: 识别含 OPEN / SHORT 关键字的文件。
    - basic:   识别含 WO / WS 关键字的文件。
    """
    zip_path = Path(zip_path)
    keywords = ("OPEN", "SHORT")
    if method == "basic":
        keywords = ("WO", "WS")
    elif method != "default":
        raise ValueError(f"method must be 'default' or 'basic', got {method!r}")

    try:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                if not name.upper().endswith(".S2P"):
                    continue
                parsed = parse_filename(name)
                if parsed.is_calibration:
                    return True
                # basic 方法额外匹配 WO / WS（parse_filename 不识别）
                if method == "basic" and any(kw in name.upper() for kw in keywords):
                    return True
    except Exception:
        logger.exception("检查 zip 校准件失败: %s", zip_path)
    return False


def _find_7z() -> str | None:
    for name in ("7z", "7za", "p7zip"):
        path = shutil.which(name)
        if path:
            return path
    return None


class StreamingExtractor:
    """用 7z 或 unzip 解压 zip，并通过 extract() 迭代器逐文件产出已落地路径。"""

    def __init__(
        self,
        zip_path: str | Path,
        target_dir: str | Path,
        exe: str | None = None,
        scan_interval: float = 1.0,
    ):
        self.zip_path = Path(zip_path)
        self.target_dir = Path(target_dir)
        self.exe = exe or _find_7z()
        self.scan_interval = scan_interval
        self._proc: subprocess.Popen | None = None

    def extract(
        self,
        progress_callback: Callable[[int], None] | None = None,
    ) -> Iterator[Path]:
        """解压并产出每个新落地的文件路径。

        实现：启动解压子进程，主线程轮询 target_dir 发现新文件；
        子进程结束后做最终扫描确保无遗漏。
        """
        self.target_dir.mkdir(parents=True, exist_ok=True)
        seen: set[str] = set()

        def _scan() -> list[Path]:
            found: list[Path] = []
            for p in self.target_dir.rglob("*"):
                if p.is_file():
                    relpath = str(p.relative_to(self.target_dir))
                    if relpath not in seen:
                        seen.add(relpath)
                        found.append(p)
            return found

        if self.exe:
            cmd = [
                self.exe,
                "x",
                "-y",
                "-bb0",
                "-o" + str(self.target_dir),
                str(self.zip_path),
            ]
        elif shutil.which("unzip"):
            cmd = [
                "unzip",
                "-q",
                "-o",
                str(self.zip_path),
                "-d",
                str(self.target_dir),
            ]
        else:
            raise RuntimeError("未安装 7z / unzip，无法流式解压")

        self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            while self._proc.poll() is None:
                for p in _scan():
                    yield p
                if progress_callback:
                    progress_callback(len(seen))
                time.sleep(self.scan_interval)
            # 最终扫描
            for p in _scan():
                yield p
            if self._proc.returncode != 0:
                stderr = (
                    self._proc.stderr.read().decode("utf-8", errors="ignore")
                    if self._proc.stderr
                    else ""
                )
                raise RuntimeError(f"解压失败 (code {self._proc.returncode}): {stderr}")
        finally:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
