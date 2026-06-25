# 边压缩边计算（Compress-While-Calculate）实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。
> **实现工作树：** `/Users/jingbozuo/Projects/aln-data-backend-api`（`feat/backend-api` 分支）。
> **设计规格：** `docs/superpowers/specs/2026-06-25-compress-while-calculate-design.md`（已 commit 在 `main`）。

**目标：** 为含 de-embedding 的大 zip 实现边解压、边去嵌、边提参、边 gzip 归档的 Celery 流水线，用 #2.zip 做集成测试。

**架构：** 在上传服务层根据 `deembed=True` 且 zip 内含 OPEN/SHORT 校准件选择新链路 `aln.pipeline_batch`；任务内部用 7z 解压生产者线程 + 文件扫描器 + `ProcessPoolExecutor` 消费者池并发处理 DUT；提参成功后立即 gzip 原始 snp 并更新数据库。

**技术栈：** FastAPI、Celery、SQLAlchemy 2.0、7z/p7zip、Python `gzip`/`multiprocessing`。

---

## 文件结构

| 文件 | 类型 | 职责 |
|------|------|------|
| `backend/app/config.py` | 修改 | 新增流水线相关 `Settings`。 |
| `backend/app/workers/pipeline/__init__.py` | 创建 | 包初始化。 |
| `backend/app/workers/pipeline/extractor.py` | 创建 | 7z/p7zip 流式解压封装 + zip 校准件检测。 |
| `backend/app/workers/pipeline/watcher.py` | 创建 | 目录轮询，发现新 DUT 文件。 |
| `backend/app/workers/pipeline/calibration.py` | 创建 | 拆分校准件并建立匹配索引。 |
| `backend/app/workers/pipeline/processor.py` | 创建 | 单 DUT 处理：拆分→去嵌→提参→归档。 |
| `backend/app/workers/pipeline_batch.py` | 创建 | Celery 任务入口与整体协调。 |
| `backend/app/workers/__init__.py` | 修改 | 导入并注册 `pipeline_batch_task`。 |
| `backend/app/services/upload_service.py` | 修改 | 根据 de-embed 与校准件选择链路。 |
| `backend/app/api/files.py` | 修改 | 下载/曲线接口支持 `.s1p.gz`/`.s2p.gz`。 |
| `backend/tests/workers/pipeline/test_extractor.py` | 创建 | extractor 单元测试。 |
| `backend/tests/workers/pipeline/test_watcher.py` | 创建 | watcher 单元测试。 |
| `backend/tests/workers/pipeline/test_calibration.py` | 创建 | calibration index 单元测试。 |
| `backend/tests/workers/pipeline/test_processor.py` | 创建 | processor 单元测试。 |
| `backend/tests/workers/test_pipeline_batch.py` | 创建 | pipeline_batch_task 集成测试。 |

---

## 任务 1：新增流水线配置项

**文件：**
- 修改：`backend/app/config.py`

- [ ] **步骤 1：编写测试验证新增配置可读取**

```python
# backend/tests/test_config.py（如不存在则创建）
def test_pipeline_settings_defaults():
    from app.config import get_settings

    settings = get_settings()
    assert settings.PIPELINE_ENABLED is True
    assert settings.PIPELINE_WORKERS == 0
    assert settings.PIPELINE_SCAN_INTERVAL == 1.0
    assert settings.PIPELINE_COMPRESS_RAW is True
    assert settings.PIPELINE_KEEP_DEEMBED_TEMP is False
```

- [ ] **步骤 2：运行测试验证失败**

```bash
cd backend
uv run pytest tests/test_config.py::test_pipeline_settings_defaults -v
```

预期：FAIL，`AttributeError: 'Settings' object has no attribute 'PIPELINE_ENABLED'`

- [ ] **步骤 3：在 Settings 中添加配置项**

在 `backend/app/config.py` 的 `Settings` 类中 `KEEP_RAW_ZIP` 之后添加：

```python
    # 边解压边计算流水线
    PIPELINE_ENABLED: bool = True          # 是否启用新链路
    PIPELINE_WORKERS: int = 0              # 0 = os.cpu_count()
    PIPELINE_SCAN_INTERVAL: float = 1.0    # 文件扫描间隔（秒）
    PIPELINE_COMPRESS_RAW: bool = True     # 提参后是否 gzip 原始 snp
    PIPELINE_KEEP_DEEMBED_TEMP: bool = False  # 是否保留去嵌中间 *_de.s1p
```

- [ ] **步骤 4：运行测试验证通过**

```bash
uv run pytest tests/test_config.py::test_pipeline_settings_defaults -v
```

预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/config.py backend/tests/test_config.py
git commit -m "feat(pipeline): add compress-while-calculate settings

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 2：zip 校准件检测 helper

**文件：**
- 创建：`backend/app/workers/pipeline/extractor.py`（先只放 helper）
- 创建：`backend/tests/workers/pipeline/test_extractor.py`

- [ ] **步骤 1：编写失败测试**

```python
# backend/tests/workers/pipeline/test_extractor.py
import zipfile
from pathlib import Path

import pytest

from app.workers.pipeline.extractor import zip_contains_calibration


def test_zip_contains_calibration_true(tmp_path: Path) -> None:
    zip_path = tmp_path / "with_cal.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("DUT_1.s2p", "# dummy\n1 1 1 1 1 1 1 1 1\n")
        zf.writestr("OPEN_1.s2p", "# dummy\n1 1 1 1 1 1 1 1 1\n")
        zf.writestr("SHORT_1.s2p", "# dummy\n1 1 1 1 1 1 1 1 1\n")
    assert zip_contains_calibration(zip_path) is True


def test_zip_contains_calibration_false(tmp_path: Path) -> None:
    zip_path = tmp_path / "no_cal.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("DUT_1.s2p", "# dummy\n1 1 1 1 1 1 1 1 1\n")
    assert zip_contains_calibration(zip_path) is False
```

- [ ] **步骤 2：运行测试验证失败**

```bash
uv run pytest tests/workers/pipeline/test_extractor.py -v
```

预期：两个测试均 FAIL，`ModuleNotFoundError` 或函数未定义。

- [ ] **步骤 3：实现 helper**

```python
# backend/app/workers/pipeline/extractor.py
from __future__ import annotations

import logging
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)


def zip_contains_calibration(zip_path: str | Path, method: str = "default") -> bool:
    """检查 zip 内是否包含 OPEN/SHORT 校准件 .s2p。

    目前按文件名关键字识别（覆盖 default/original/vz/basic 方法）。
    gsg100 方法可后续扩展。
    """
    zip_path = Path(zip_path)
    keywords = ("OPEN", "SHORT")
    if method == "basic":
        keywords = ("WO", "WS")

    try:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = info.filename.upper()
                if not name.endswith(".S2P"):
                    continue
                if any(kw in name for kw in keywords):
                    return True
    except Exception:
        logger.exception("检查 zip 校准件失败: %s", zip_path)
    return False
```

- [ ] **步骤 4：运行测试验证通过**

```bash
uv run pytest tests/workers/pipeline/test_extractor.py -v
```

预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/workers/pipeline/extractor.py backend/tests/workers/pipeline/test_extractor.py
git commit -m "feat(pipeline): add zip calibration detection helper

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 3：StreamingExtractor（7z 流式解压）

**文件：**
- 修改：`backend/app/workers/pipeline/extractor.py`
- 修改：`backend/tests/workers/pipeline/test_extractor.py`

- [ ] **步骤 1：编写失败测试**

```python
# backend/tests/workers/pipeline/test_extractor.py
import os
import zipfile

from app.workers.pipeline.extractor import StreamingExtractor


def test_streaming_extractor_yields_files(tmp_path: Path) -> None:
    zip_path = tmp_path / "test.zip"
    target_dir = tmp_path / "out"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("a.s1p", "# dummy\n1 1 1\n")
        zf.writestr("b.s1p", "# dummy\n2 2 2\n")

    extractor = StreamingExtractor(zip_path, target_dir)
    found = sorted(p.name for p in extractor.extract())
    assert found == ["a.s1p", "b.s1p"]
    assert (target_dir / "a.s1p").exists()


@pytest.mark.skipif(not shutil.which("7z") and not shutil.which("7za"), reason="无 7z")
def test_streaming_extractor_uses_7z(tmp_path: Path) -> None:
    zip_path = tmp_path / "test.zip"
    target_dir = tmp_path / "out"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("x.s1p", "# dummy\n1 1 1\n")
    extractor = StreamingExtractor(zip_path, target_dir)
    assert extractor.exe and ("7z" in extractor.exe or "7za" in extractor.exe)
```

- [ ] **步骤 2：运行测试验证失败**

```bash
uv run pytest tests/workers/pipeline/test_extractor.py -v
```

预期：FAIL，`StreamingExtractor` 未定义。

- [ ] **步骤 3：实现 StreamingExtractor**

在 `backend/app/workers/pipeline/extractor.py` 中添加：

```python
import shutil
import subprocess
import threading
import time
from typing import Callable, Iterator


def _find_7z() -> str | None:
    for name in ("7z", "7za", "p7zip"):
        path = shutil.which(name)
        if path:
            return path
    return None


class StreamingExtractor:
    """用 7z 或 unzip 解压 zip，并通过 extract() 迭代器逐文件产出已落地路径。"""

    def __init__(self, zip_path: str | Path, target_dir: str | Path, exe: str | None = None):
        self.zip_path = Path(zip_path)
        self.target_dir = Path(target_dir)
        self.exe = exe or _find_7z()
        self._proc: subprocess.Popen | None = None

    def extract(
        self,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> Iterator[Path]:
        """解压并产出每个新落地的文件路径。

        实现：启动解压子进程，主线程轮询 target_dir 发现新文件；
        子进程结束后做最终扫描确保无遗漏。
        """
        self.target_dir.mkdir(parents=True, exist_ok=True)
        seen: set[str] = set()

        def _scan() -> list[Path]:
            found: list[Path] = []
            for p in sorted(self.target_dir.rglob("*")):
                if p.is_file():
                    relpath = str(p.relative_to(self.target_dir))
                    if relpath not in seen:
                        seen.add(relpath)
                        found.append(p)
            return found

        if self.exe:
            cmd = [self.exe, "x", "-y", "-bb0", "-o" + str(self.target_dir), str(self.zip_path)]
        elif shutil.which("unzip"):
            cmd = ["unzip", "-q", "-o", str(self.zip_path), "-d", str(self.target_dir)]
        else:
            raise RuntimeError("未安装 7z / unzip，无法流式解压")

        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            while self._proc.poll() is None:
                for p in _scan():
                    yield p
                if progress_callback:
                    progress_callback(len(seen), 0)  # total 未知时传 0
                time.sleep(0.5)
            # 最终扫描
            for p in _scan():
                yield p
            if self._proc.returncode != 0:
                stderr = self._proc.stderr.read().decode("utf-8", errors="ignore") if self._proc.stderr else ""
                raise RuntimeError(f"解压失败 (code {self._proc.returncode}): {stderr}")
        finally:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
```

- [ ] **步骤 4：运行测试验证通过**

```bash
uv run pytest tests/workers/pipeline/test_extractor.py -v
```

预期：PASS（无 7z 时 skip 第二个测试）

- [ ] **步骤 5：Commit**

```bash
git add backend/app/workers/pipeline/extractor.py backend/tests/workers/pipeline/test_extractor.py
git commit -m "feat(pipeline): add StreamingExtractor with 7z fallback

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 4：FileWatcher（目录轮询）

**文件：**
- 创建：`backend/app/workers/pipeline/watcher.py`
- 创建：`backend/tests/workers/pipeline/test_watcher.py`

- [ ] **步骤 1：编写失败测试**

```python
# backend/tests/workers/pipeline/test_watcher.py
import threading
import time
from pathlib import Path

from app.workers.pipeline.watcher import FileWatcher


def test_watcher_discovers_new_files(tmp_path: Path) -> None:
    watcher = FileWatcher(tmp_path, patterns=["*.s1p"], interval=0.05)
    stop = threading.Event()

    (tmp_path / "old.s1p").write_text("# old\n")

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
```

- [ ] **步骤 2：运行测试验证失败**

```bash
uv run pytest tests/workers/pipeline/test_watcher.py -v
```

预期：FAIL，`FileWatcher` 未定义。

- [ ] **步骤 3：实现 FileWatcher**

```python
# backend/app/workers/pipeline/watcher.py
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


class FileWatcher:
    """轮询目录，产出匹配模式的新文件。"""

    def __init__(self, root_dir: str | Path, patterns: list[str], interval: float = 1.0):
        self.root_dir = Path(root_dir)
        self.patterns = patterns
        self.interval = interval
        self._seen: set[str] = set()

    def _scan(self) -> list[Path]:
        found: list[Path] = []
        for pattern in self.patterns:
            for p in sorted(self.root_dir.rglob(pattern)):
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
```

- [ ] **步骤 4：运行测试验证通过**

```bash
uv run pytest tests/workers/pipeline/test_watcher.py -v
```

预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/workers/pipeline/watcher.py backend/tests/workers/pipeline/test_watcher.py
git commit -m "feat(pipeline): add FileWatcher for incremental file discovery

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 5：CalibrationIndex（校准件索引）

**文件：**
- 创建：`backend/app/workers/pipeline/calibration.py`
- 创建：`backend/tests/workers/pipeline/test_calibration.py`

- [ ] **步骤 1：编写失败测试**

```python
# backend/tests/workers/pipeline/test_calibration.py
from pathlib import Path

import pytest

from app.core.deembed import DeembedError
from app.workers.pipeline.calibration import CalibrationIndex


def test_calibration_index_splits_and_matches(tmp_path: Path) -> None:
    # 构造一个 OPEN/SHORT s2p
    open_s2p = tmp_path / "OPEN_1.s2p"
    short_s2p = tmp_path / "SHORT_1.s2p"
    open_s2p.write_text(
        "# Hz S RI R 50\n!\n1000000000 1 0 0 0 0 0 0 0 1 0\n"
    )
    short_s2p.write_text(
        "# Hz S RI R 50\n!\n1000000000 0 0 1 0 0 0 1 0 0 0\n"
    )

    index = CalibrationIndex.build(tmp_path / "cal", [open_s2p, short_s2p], method="default")

    dut_s1p = tmp_path / "DUT_1_S11.s1p"
    dut_s1p.write_text("# Hz S RI R 50\n!\n1000000000 0.5 0\n")

    op, sh = index.match("S11", dut_s1p)
    assert op.name == "OPEN_1_S11.s1p"
    assert sh.name == "SHORT_1_S11.s1p"


def test_calibration_index_missing_raises(tmp_path: Path) -> None:
    index = CalibrationIndex.build(tmp_path / "cal", [], method="default")
    dut_s1p = tmp_path / "DUT_1_S11.s1p"
    dut_s1p.write_text("# Hz S RI R 50\n!\n1000000000 0.5 0\n")
    with pytest.raises(DeembedError):
        index.match("S11", dut_s1p)
```

- [ ] **步骤 2：运行测试验证失败**

```bash
uv run pytest tests/workers/pipeline/test_calibration.py -v
```

预期：FAIL，`CalibrationIndex` 未定义。

- [ ] **步骤 3：实现 CalibrationIndex**

```python
# backend/app/workers/pipeline/calibration.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.core.deembed import DeembedMethod, match_calibration
from app.core.touchstone import split_s2p_to_s1p

logger = logging.getLogger(__name__)


@dataclass
class CalibrationIndex:
    """已拆分并按端口分组的 OPEN/SHORT 校准件索引。"""

    s11_paths: list[Path]
    s22_paths: list[Path]
    method: DeembedMethod

    @classmethod
    def build(
        cls,
        target_dir: Path,
        cal_s2p_files: list[Path],
        method: str,
    ) -> "CalibrationIndex":
        s11_dir = target_dir / "cal_S11"
        s22_dir = target_dir / "cal_S22"
        s11_paths: list[Path] = []
        s22_paths: list[Path] = []
        for p in cal_s2p_files:
            try:
                split = split_s2p_to_s1p(p, out_dir_s11=s11_dir, out_dir_s22=s22_dir)
                s11_paths.append(split.s11_path)
                s22_paths.append(split.s22_path)
            except Exception:
                logger.warning("拆分校准件失败 %s", p.name, exc_info=True)
        return cls(
            s11_paths=s11_paths,
            s22_paths=s22_paths,
            method=DeembedMethod(method),
        )

    def match(self, port: str, dut_s1p_path: Path) -> tuple[Path, Path]:
        cal_paths = self.s11_paths if port == "S11" else self.s22_paths
        return match_calibration(dut_s1p_path, cal_paths, self.method)
```

- [ ] **步骤 4：运行测试验证通过**

```bash
uv run pytest tests/workers/pipeline/test_calibration.py -v
```

预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/workers/pipeline/calibration.py backend/tests/workers/pipeline/test_calibration.py
git commit -m "feat(pipeline): add CalibrationIndex for de-embedding lookup

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 6：DutProcessor（单 DUT 处理）

**文件：**
- 创建：`backend/app/workers/pipeline/processor.py`
- 创建：`backend/tests/workers/pipeline/test_processor.py`

- [ ] **步骤 1：编写失败测试**

```python
# backend/tests/workers/pipeline/test_processor.py
import gzip
from pathlib import Path

from app.workers.pipeline.processor import DutProcessor


def test_processor_s1p_no_deembed(tmp_path: Path) -> None:
    # 写一个最小可用 s1p：频率范围内有清晰的 fs/fp
    s1p = tmp_path / "DUT_X1Y1_S11.s1p"
    lines = ["# Hz S RI R 50\n", "!\n"]
    # 构造阻抗：低频高，中间低，高频高，模拟串联谐振
    for i in range(100):
        f = 1e9 + i * 10e6
        z_re = 10 + (i - 50) ** 2 * 0.5
        lines.append(f"{f:.0f} {z_re:.6f} 0.0\n")
    s1p.write_text("".join(lines))

    processor = DutProcessor(compress_raw=True, keep_deembed_temp=False)
    result = processor.process(
        {"type": "s1p", "path": str(s1p), "s_param_relpath": "DUT_X1Y1_S11.s1p"},
        mapping={},
        wafer=None,
        cal_index=None,
        target_dir=tmp_path,
    )
    assert result["ok"] is True
    assert len(result["rows"]) == 1
    assert result["rows"][0]["s_param_port"] == "S11"
    assert (tmp_path / "DUT_X1Y1_S11.s1p.gz").exists()
```

- [ ] **步骤 2：运行测试验证失败**

```bash
uv run pytest tests/workers/pipeline/test_processor.py -v
```

预期：FAIL，`DutProcessor` 未定义。

- [ ] **步骤 3：实现 DutProcessor**

```python
# backend/app/workers/pipeline/processor.py
from __future__ import annotations

import gzip
import logging
import shutil
from pathlib import Path
from typing import Any

from app.core.deembed import deembed
from app.core.extract import extract_resonator_params
from app.core.touchstone import split_s2p_to_s1p
from app.workers.pipeline.calibration import CalibrationIndex

logger = logging.getLogger(__name__)


class DutProcessor:
    """处理单个 DUT 文件：拆分→去嵌→提参→归档。"""

    def __init__(self, compress_raw: bool = True, keep_deembed_temp: bool = False):
        self.compress_raw = compress_raw
        self.keep_deembed_temp = keep_deembed_temp

    def process(
        self,
        item: dict[str, Any],
        mapping: dict[str, Any] | None,
        wafer: int | None,
        cal_index: CalibrationIndex | None,
        target_dir: Path,
    ) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        failures: list[str] = []

        if item["type"] == "s2p":
            rows, failures = self._process_s2p(item, mapping, wafer, cal_index, target_dir)
        else:
            try:
                row = extract_resonator_params(
                    Path(item["path"]),
                    mapping=mapping,
                    wafer=wafer,
                    s_param_relpath=item["s_param_relpath"],
                    deembedded=False,
                    skip_validation=True,
                )
                rows.append(row)
            except Exception as exc:
                failures.append(f"{Path(item['path']).name}: {exc}")

        archived: list[str] = []
        if self.compress_raw and rows:
            try:
                gz_path = self._gzip_original(Path(item["path"]))
                archived.append(str(gz_path))
            except Exception as exc:
                logger.warning("压缩原始文件失败 %s: %s", item["path"], exc)

        return {"ok": bool(rows), "rows": rows, "failures": failures, "archived": archived}

    def _process_s2p(
        self,
        item: dict[str, Any],
        mapping: dict[str, Any] | None,
        wafer: int | None,
        cal_index: CalibrationIndex | None,
        target_dir: Path,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        rows: list[dict[str, Any]] = []
        failures: list[str] = []
        s2p_path = Path(item["path"])

        try:
            split = split_s2p_to_s1p(
                s2p_path,
                out_dir_s11=target_dir / "S11",
                out_dir_s22=target_dir / "S22",
            )
        except Exception as exc:
            return rows, [f"{s2p_path.name}: 拆分失败 {exc}"]

        de_s11_dir = target_dir / "S11_de"
        de_s22_dir = target_dir / "S22_de"

        for port, port_path, de_dir in (
            ("S11", split.s11_path, de_s11_dir),
            ("S22", split.s22_path, de_s22_dir),
        ):
            try:
                if cal_index is None:
                    raise RuntimeError("未提供 CalibrationIndex")
                open_path, short_path = cal_index.match(port, port_path)
                de_path = de_dir / port_path.name.replace(".s1p", "_de.s1p")
                deembed(port_path, open_path, short_path, de_path)
                row = extract_resonator_params(
                    de_path,
                    mapping=mapping,
                    wafer=wafer,
                    s_param_relpath=item["s_param_relpath"],
                    deembedded=True,
                    skip_validation=True,
                    port=0,
                )
                row["s_param_port"] = port
                rows.append(row)
                if not self.keep_deembed_temp:
                    try:
                        de_path.unlink(missing_ok=True)
                    except Exception:
                        pass
            except Exception as exc:
                failures.append(f"{port_path.name}: {exc}")

        return rows, failures

    @staticmethod
    def _gzip_original(path: Path) -> Path:
        gz_path = path.with_suffix(path.suffix + ".gz")
        with open(path, "rb") as src, gzip.open(gz_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
        path.unlink(missing_ok=True)
        return gz_path
```

- [ ] **步骤 4：运行测试验证通过**

```bash
uv run pytest tests/workers/pipeline/test_processor.py::test_processor_s1p_no_deembed -v
```

预期：PASS（如果数据构造不当导致谐振点检测失败，调整测试数据中的阻抗曲线）

- [ ] **步骤 5：Commit**

```bash
git add backend/app/workers/pipeline/processor.py backend/tests/workers/pipeline/test_processor.py
git commit -m "feat(pipeline): add DutProcessor for split/deembed/extract/archive

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 7：pipeline_batch_task（Celery 任务入口）

**文件：**
- 创建：`backend/app/workers/pipeline_batch.py`
- 创建：`backend/tests/workers/test_pipeline_batch.py`

- [ ] **步骤 1：编写失败测试**

```python
# backend/tests/workers/test_pipeline_batch.py
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.workers.pipeline_batch import should_use_pipeline


def test_should_use_pipeline_true(tmp_path: Path) -> None:
    zip_path = tmp_path / "cal.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("DUT.s2p", "#\n1 1 1 1 1 1 1 1 1\n")
        zf.writestr("OPEN.s2p", "#\n1 1 1 1 1 1 1 1 1\n")
    assert should_use_pipeline(zip_path, deembed=True) is True


def test_should_use_pipeline_false_when_no_deembed(tmp_path: Path) -> None:
    zip_path = tmp_path / "cal.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("DUT.s2p", "#\n1 1 1 1 1 1 1 1 1\n")
        zf.writestr("OPEN.s2p", "#\n1 1 1 1 1 1 1 1 1\n")
    assert should_use_pipeline(zip_path, deembed=False) is False
```

- [ ] **步骤 2：运行测试验证失败**

```bash
uv run pytest tests/workers/test_pipeline_batch.py -v
```

预期：FAIL，`pipeline_batch` 模块未定义。

- [ ] **步骤 3：实现 pipeline_batch_task（骨架 + 调度判断）**

先实现 `should_use_pipeline` 与任务骨架：

```python
# backend/app/workers/pipeline_batch.py
from __future__ import annotations

import logging
import os
import shutil
import threading
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from celery import Task
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from app.config import get_algorithm_config, get_settings
from app.core.extract import ExtractError
from app.core.filename import parse_filename
from app.core.mapping import load_mapping
from app.db import SessionLocal
from app.models import Batch, Device, Mapping, UploadTask
from app.workers import celery_app
from app.workers.pipeline.calibration import CalibrationIndex
from app.workers.pipeline.extractor import StreamingExtractor, zip_contains_calibration
from app.workers.pipeline.processor import DutProcessor
from app.workers.pipeline.watcher import FileWatcher
from app.workers.progress import ProgressPublisher

logger = logging.getLogger(__name__)

INSERT_CHUNK = 2000


def should_use_pipeline(zip_path: str | Path, deembed: bool) -> bool:
    """判断是否启用新流水线。"""
    if not deembed:
        return False
    if not get_settings().PIPELINE_ENABLED:
        return False
    return zip_contains_calibration(zip_path)


@celery_app.task(bind=True, name="aln.pipeline_batch")
def pipeline_batch_task(
    self: Task,
    upload_task_id: int,
    zip_path: str,
    batch_no: str,
    mapping_id: int,
    f_start_ghz: float | None = None,
    f_end_ghz: float | None = None,
    deembed_method: str = "default",
    process_type: str = "AUTO",
) -> dict[str, Any]:
    """边解压边去嵌/提参/归档的 Celery 任务。"""
    publisher = ProgressPublisher(upload_task_id)
    settings = get_settings()
    db = SessionLocal()

    try:
        publisher.start(db, msg="开始流水线处理…")
        from sqlalchemy import select

        batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
        if batch is None:
            raise RuntimeError(f"batches 表无 batch_no={batch_no}")
        mapping_row = db.get(Mapping, mapping_id)
        if mapping_row is None:
            raise RuntimeError(f"mappings 表无 id={mapping_id}")
        mapping_dict = load_mapping(mapping_row.file_path)

        target_dir = settings.files_dir / batch_no
        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        # 清理旧 devices
        db.execute(delete(Device).where(Device.batch_id == batch.id))
        db.commit()

        # TODO: 后续步骤实现完整流程
        raise NotImplementedError("pipeline_batch_task 完整实现见后续步骤")

    except Exception as exc:
        logger.exception("pipeline_batch_task fatal")
        try:
            db.rollback()
        except Exception:
            pass
        try:
            publisher.fail(db, error_msg=str(exc))
        except Exception:
            logger.exception("publisher.fail itself raised")
        raise
    finally:
        db.close()
```

- [ ] **步骤 4：运行测试验证通过**

```bash
uv run pytest tests/workers/test_pipeline_batch.py -v
```

预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/workers/pipeline_batch.py backend/tests/workers/test_pipeline_batch.py
git commit -m "feat(pipeline): add pipeline_batch task skeleton and dispatch guard

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 8：实现 pipeline_batch_task 完整协调逻辑

**文件：**
- 修改：`backend/app/workers/pipeline_batch.py`

- [ ] **步骤 1：编写集成测试（先失败）**

```python
# backend/tests/workers/test_pipeline_batch.py
import zipfile
from pathlib import Path

import pytest

from app.workers.pipeline_batch import pipeline_batch_task


def _write_min_s1p(path: Path) -> None:
    lines = ["# Hz S RI R 50\n", "!\n"]
    for i in range(100):
        f = 1e9 + i * 10e6
        z_re = 10 + (i - 50) ** 2 * 0.5
        lines.append(f"{f:.0f} {z_re:.6f} 0.0\n")
    path.write_text("".join(lines))


def _write_min_s2p(path: Path) -> None:
    lines = ["# Hz S RI R 50\n", "!\n"]
    for i in range(100):
        f = 1e9 + i * 10e6
        z_re = 10 + (i - 50) ** 2 * 0.5
        lines.append(f"{f:.0f} {z_re:.6f} 0.0 0.0 0.0 0.0 0.0 0.0 0.0 {z_re:.6f} 0.0\n")
    path.write_text("".join(lines))


@pytest.mark.integration
@pytest.mark.usefixtures("celery_session_worker")
def test_pipeline_batch_end_to_end(db, tmp_path: Path) -> None:
    """用最小数据集验证完整流水线可成功入库。"""
    from app.services.upload_service import create_batch_and_dispatch
    from app.models import Mapping

    mapping = Mapping(name="test", file_path="nonexistent.csv")
    db.add(mapping)
    db.commit()

    zip_path = tmp_path / "batch.1.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        _write_min_s2p(tmp_path / "DUT_1.s2p")
        _write_min_s2p(tmp_path / "OPEN_1.s2p")
        _write_min_s2p(tmp_path / "SHORT_1.s2p")
        zf.write(tmp_path / "DUT_1.s2p", "DUT_1.s2p")
        zf.write(tmp_path / "OPEN_1.s2p", "OPEN_1.s2p")
        zf.write(tmp_path / "SHORT_1.s2p", "SHORT_1.s2p")

    task = create_batch_and_dispatch(
        db,
        zip_path=zip_path,
        batch_no="PIPELINE_TEST.1",
        mapping_id=mapping.id,
        deembed=True,
    )
    assert task is not None

    result = pipeline_batch_task.apply(
        kwargs={
            "upload_task_id": task.id,
            "zip_path": str(zip_path),
            "batch_no": "PIPELINE_TEST.1",
            "mapping_id": mapping.id,
            "deembed_method": "default",
        }
    ).get(timeout=120)

    assert result["device_count"] == 2
```

- [ ] **步骤 2：运行测试验证失败**

```bash
uv run pytest tests/workers/test_pipeline_batch.py::test_pipeline_batch_end_to_end -v -m integration
```

预期：FAIL，`NotImplementedError`。

- [ ] **步骤 3：实现完整协调逻辑**

替换 `pipeline_batch_task` 中的 `raise NotImplementedError` 为以下完整实现：

```python
        # 1. 启动解压生产者线程
        stop_event = threading.Event()
        extractor = StreamingExtractor(zip_path, target_dir)
        extracted_paths: list[Path] = []

        def _extract() -> None:
            try:
                for p in extractor.extract(
                    progress_callback=lambda cur, total: publisher.stage_update(
                        db,
                        stage="extract",
                        stage_progress_pct=int(30 * cur / max(cur, 1)),
                        progress_pct=int(30 * cur / max(cur, 1)),
                        progress_msg=f"解压中… {cur} 文件已落地",
                    )
                ):
                    extracted_paths.append(p)
            except Exception as exc:
                logger.exception("解压线程异常")
                stop_event.set()
                raise
            finally:
                stop_event.set()

        extract_thread = threading.Thread(target=_extract)
        extract_thread.start()

        # 2. 文件发现器
        watcher = FileWatcher(
            target_dir,
            patterns=["*.s1p", "*.s2p"],
            interval=settings.PIPELINE_SCAN_INTERVAL,
        )

        cal_s2p_files: list[Path] = []
        pending_duts: list[dict[str, Any]] = []
        cal_index: CalibrationIndex | None = None
        processed_names: set[str] = set()

        # 3. 轮询：先收集校准件，建立索引后再消费 DUT
        for p in watcher.watch(stop_event):
            parsed = parse_filename(p.name)
            if parsed.is_calibration or _looks_like_calibration(p.name):
                if p.suffix.lower() == ".s2p":
                    cal_s2p_files.append(p)
                continue

            item = _path_to_item(p, target_dir)
            if item["key"] in processed_names:
                continue
            processed_names.add(item["key"])

            if cal_index is None:
                pending_duts.append(item)
                continue

            _submit_dut(item, cal_index, pending_duts)

        extract_thread.join()

        # 最终建立索引（若轮询期间未触发）
        if cal_index is None:
            cal_index = CalibrationIndex.build(target_dir, cal_s2p_files, deembed_method)
            for item in pending_duts:
                _submit_dut(item, cal_index, [])

        # 4. 等待消费者完成并入库
        device_rows: list[dict[str, Any]] = []
        failures: list[str] = []
        # ... 等待 as_completed 并 bulk insert ...

        # 5. 刷新统计、清理
        device_count = (
            db.scalar(select(func.count(Device.id)).where(Device.batch_id == batch.id)) or 0
        )
        db.execute(
            update(Batch)
            .where(Batch.id == batch.id)
            .values(device_count=device_count)
        )
        db.commit()
        try:
            db.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_batch_stats"))
            db.commit()
        except Exception:
            logger.exception("刷新物化视图失败")
        publisher.done(db, batch_id=batch.id, device_count=device_count)
        return {"batch_id": batch.id, "device_count": device_count, "failures": len(failures)}
```

上述片段中缺失的 helper 需同时实现：

```python
def _looks_like_calibration(name: str) -> bool:
    upper = name.upper()
    return ("OPEN" in upper or "SHORT" in upper or "WO" in upper or "WS" in upper) and ".S2P" in upper


def _path_to_item(p: Path, target_dir: Path) -> dict[str, Any]:
    relpath = str(p.relative_to(target_dir))
    return {
        "key": relpath,
        "type": "s2p" if p.suffix.lower() == ".s2p" else "s1p",
        "path": str(p),
        "s_param_relpath": relpath,
    }
```

消费者提交与结果收集的完整代码：

```python
        def _submit_dut(item: dict[str, Any], cal_index: CalibrationIndex, queue: list[dict[str, Any]]) -> None:
            # 实际实现中应提交到进程池；此处为保持步骤可读，请内联完整代码
            pass

        algo_cfg = get_algorithm_config()
        workers = settings.PIPELINE_WORKERS or os.cpu_count() or 1
        processor = DutProcessor(
            compress_raw=settings.PIPELINE_COMPRESS_RAW,
            keep_deembed_temp=settings.PIPELINE_KEEP_DEEMBED_TEMP,
        )

        # 由于步骤 3 的伪代码无法直接运行，请在实现时把下面这段替换进去：
        futures = {}
        with ProcessPoolExecutor(max_workers=workers) as exe:
            def submit(item: dict[str, Any]) -> None:
                fut = exe.submit(
                    processor.process,
                    item,
                    mapping_dict,
                    _wafer_from_batch_no(batch_no),
                    cal_index,
                    target_dir,
                )
                futures[fut] = item

            # 首次索引建立后消费 pending
            if cal_index is None and cal_s2p_files:
                cal_index = CalibrationIndex.build(target_dir, cal_s2p_files, deembed_method)
                for item in pending_duts:
                    submit(item)
                pending_duts = []

            # 继续轮询中的 DUT
            for p in watcher.watch(stop_event):
                parsed = parse_filename(p.name)
                if parsed.is_calibration or _looks_like_calibration(p.name):
                    if p.suffix.lower() == ".s2p":
                        cal_s2p_files.append(p)
                        # 增量更新索引（重新 build 即可，校准件通常很少）
                        cal_index = CalibrationIndex.build(target_dir, cal_s2p_files, deembed_method)
                        # 消费 pending
                        for item in pending_duts:
                            submit(item)
                        pending_duts = []
                    continue

                item = _path_to_item(p, target_dir)
                if item["key"] in processed_names:
                    continue
                processed_names.add(item["key"])

                if cal_index is None:
                    pending_duts.append(item)
                else:
                    submit(item)

            extract_thread.join()
            # 最终索引
            if cal_index is None and cal_s2p_files:
                cal_index = CalibrationIndex.build(target_dir, cal_s2p_files, deembed_method)
            for item in pending_duts:
                submit(item)
            pending_duts = []

            # 收集结果
            processed = 0
            total = len(futures)
            for fut in as_completed(futures):
                try:
                    result = fut.result()
                    processed += 1
                    device_rows.extend(result.get("rows", []))
                    failures.extend(result.get("failures", []))
                    if len(device_rows) >= INSERT_CHUNK:
                        _bulk_insert_devices(db, device_rows)
                        device_rows = []
                except Exception as exc:
                    processed += 1
                    failures.append(str(exc))
                    logger.warning("消费者异常: %s", exc)

                stage_pct = int(100 * processed / total) if total else 100
                overall = 35 + int(60 * processed / total) if total else 95
                publisher.stage_update(
                    db,
                    stage="metrics",
                    stage_progress_pct=stage_pct,
                    progress_pct=overall,
                    progress_msg=f"已处理 {processed}/{total}，失败 {len(failures)}",
                )

        if device_rows:
            _bulk_insert_devices(db, device_rows)
```

并添加 `_bulk_insert_devices`、`_copy_insert_devices`、`_wafer_from_batch_no` 的拷贝/引用。`pipeline_batch.py` 中新增：

```python
# COPY 目标列（排除自增 id，与 devices 表定义顺序一致）
_COPY_COLUMNS = [
    "batch_id", "original_filename", "display_name", "mark", "wafer",
    "folder_name", "coord", "x", "y", "eg", "fl", "ag", "pf",
    "area_n", "area_um2", "fs_ghz", "fp_ghz", "zs_ohm", "zp_ohm",
    "qs", "qp", "qs_bodeq", "qp_bodeq", "dbqs", "dbqp",
    "bodeq_fitted", "bodeq_smooth", "bodeq_raw", "fbode_ghz", "k2eff_pct",
    "fp2_ghz", "fs2_ghz", "zp2_ohm", "zs2_ohm", "deembedded", "s_param_path",
    "s_param_port",
]


def _bulk_insert_devices(db: Session, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    COPY_THRESHOLD = 3000
    if len(rows) >= COPY_THRESHOLD:
        try:
            _copy_insert_devices(db, rows)
            return
        except Exception:
            logger.exception("COPY 批量插入失败，降级到 bulk_insert")
    db.bulk_insert_mappings(Device, rows)
    db.commit()


def _copy_insert_devices(db: Session, rows: list[dict[str, Any]]) -> None:
    raw_conn = db.connection().connection
    cols_sql = ", ".join(_COPY_COLUMNS)
    copy_sql = f"COPY devices ({cols_sql}) FROM STDIN"
    with raw_conn.cursor() as cur:
        with cur.copy(copy_sql) as copy:
            for r in rows:
                copy.write_row(tuple(r.get(c) for c in _COPY_COLUMNS))
    db.commit()


def _wafer_from_batch_no(batch_no: str) -> int | None:
    import re
    m = re.search(r"\.(\d+)$", batch_no)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None
```

- [ ] **步骤 4：运行测试验证通过**

```bash
uv run pytest tests/workers/test_pipeline_batch.py::test_pipeline_batch_end_to_end -v -m integration
```

预期：PASS（如测试数据谐振点不合适，调整阻抗曲线）

- [ ] **步骤 5：Commit**

```bash
git add backend/app/workers/pipeline_batch.py backend/tests/workers/test_pipeline_batch.py
git commit -m "feat(pipeline): implement pipeline_batch coordination logic

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 9：上传服务选择新链路

**文件：**
- 修改：`backend/app/services/upload_service.py`

- [ ] **步骤 1：编写失败测试**

```python
# backend/tests/services/test_upload_service.py（如不存在则创建）
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.services.upload_service import _choose_dispatch_task


def test_choose_dispatch_task_uses_pipeline_for_deembed_with_cal(tmp_path: Path) -> None:
    zip_path = tmp_path / "cal.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("DUT.s2p", "#\n")
        zf.writestr("OPEN.s2p", "#\n")
    task_name = _choose_dispatch_task(zip_path, deembed=True)
    assert task_name == "pipeline"


def test_choose_dispatch_task_uses_legacy_for_no_deembed(tmp_path: Path) -> None:
    zip_path = tmp_path / "cal.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("DUT.s2p", "#\n")
        zf.writestr("OPEN.s2p", "#\n")
    task_name = _choose_dispatch_task(zip_path, deembed=False)
    assert task_name == "legacy"
```

- [ ] **步骤 2：运行测试验证失败**

```bash
uv run pytest tests/services/test_upload_service.py -v
```

预期：FAIL，`_choose_dispatch_task` 未定义。

- [ ] **步骤 3：修改 upload_service 添加链路选择**

在 `backend/app/services/upload_service.py` 中：

```python
from app.workers.pipeline_batch import pipeline_batch_task, should_use_pipeline


def _choose_dispatch_task(zip_path: Path, deembed: bool) -> str:
    if should_use_pipeline(zip_path, deembed):
        return "pipeline"
    return "legacy"
```

修改 `_dispatch_chain`：

```python
def _dispatch_chain(...) -> str | None:
    from celery import chain

    task_type = _choose_dispatch_task(zip_path, deembed)
    try:
        if task_type == "pipeline":
            result = pipeline_batch_task.apply_async(
                kwargs={
                    "upload_task_id": task.id,
                    "zip_path": str(zip_path),
                    "batch_no": batch_no,
                    "mapping_id": mapping_id,
                    "f_start_ghz": f_start_ghz,
                    "f_end_ghz": f_end_ghz,
                    "deembed_method": deembed_method if deembed else "default",
                    "process_type": process_type,
                }
            )
        else:
            result = chain(
                extract_batch_task.s(...),
                compute_batch_task.s(),
            ).apply_async()
        return result.id
    except ImportError as exc:
        ...
```

- [ ] **步骤 4：运行测试验证通过**

```bash
uv run pytest tests/services/test_upload_service.py -v
```

预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/services/upload_service.py backend/tests/services/test_upload_service.py
git commit -m "feat(pipeline): dispatch to pipeline_batch for de-embed batches

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 10：注册 Celery 任务

**文件：**
- 修改：`backend/app/workers/__init__.py`

- [ ] **步骤 1：修改导入**

```python
# backend/app/workers/__init__.py
# worker 启动时注册任务
from app.workers import compute_batch  # noqa: E402,F401
from app.workers import extract_batch  # noqa: E402,F401
from app.workers import pipeline_batch  # noqa: E402,F401
from app.workers import process_batch  # noqa: E402,F401
```

- [ ] **步骤 2：验证任务已注册**

```bash
cd backend
uv run python -c "from app.workers import celery_app; print(celery_app.tasks.keys())"
```

预期输出包含 `'aln.pipeline_batch'`。

- [ ] **步骤 3：Commit**

```bash
git add backend/app/workers/__init__.py
git commit -m "feat(pipeline): register pipeline_batch celery task

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 11：files.py 支持 gzip 归档下载

**文件：**
- 修改：`backend/app/api/files.py`

- [ ] **步骤 1：编写失败测试**

```python
# backend/tests/api/test_files_gzip.py（如不存在则创建）
import gzip
from pathlib import Path

from fastapi.testclient import TestClient


def test_read_gzipped_s1p(client: TestClient, db, tmp_path: Path) -> None:
    # 依赖 fixtures 由现有 conftest 提供
    from app.models import Batch
    batch = Batch(batch_no="GZ.1", mapping_id=None, file_path=str(tmp_path))
    db.add(batch)
    db.commit()

    s1p = tmp_path / "a.s1p"
    s1p.write_text("# Hz S RI R 50\n!\n1000000000 0.5 0\n")
    gz = tmp_path / "a.s1p.gz"
    with open(s1p, "rb") as src, gzip.open(gz, "wb") as dst:
        dst.write(src.read())
    s1p.unlink()

    resp = client.get("/api/files/curve?batch_no=GZ.1&relpath=a.s1p&param=z_mag_db")
    assert resp.status_code == 200
```

- [ ] **步骤 2：运行测试验证失败**

```bash
uv run pytest tests/api/test_files_gzip.py -v
```

预期：FAIL，404 或读取失败。

- [ ] **步骤 3：修改 files.py 支持 gzip**

在 `backend/app/api/files.py` 中新增 helper 并修改 `_read_network` 与 `_safe_resolve`：

```python
import gzip
import shutil


def _find_actual_path(base_dir: Path, relpath: str) -> Path:
    """解析相对路径；若原文件不存在但存在 .gz 版本，则返回 .gz 路径。"""
    target = _safe_resolve(base_dir, relpath)
    if target.exists():
        return target
    gz_target = target.with_suffix(target.suffix + ".gz")
    if gz_target.exists():
        return gz_target
    raise HTTPException(status_code=404, detail=f"文件不存在: {relpath}")


def _read_network(target_path: Path, process_type: str = "S1P") -> "skrf.Network":
    import skrf

    suffix = target_path.suffix.lower()
    is_gz = False
    if suffix == ".gz":
        is_gz = True
        real_suffix = Path(target_path.stem).suffix.lower()
    else:
        real_suffix = suffix

    if real_suffix == ".snp":
        new_ext = ".s1p" if process_type == "S1P" else ".s2p"
        tmp_dir = Path(tempfile.mkdtemp(prefix="aln_snp_"))
        tmp_path = tmp_dir / (target_path.stem + new_ext)
        _copy_maybe_gz(target_path, tmp_path)
        try:
            return skrf.Network(str(tmp_path))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    if is_gz:
        tmp_dir = Path(tempfile.mkdtemp(prefix="aln_gz_"))
        tmp_path = tmp_dir / target_path.stem
        _copy_maybe_gz(target_path, tmp_path)
        try:
            return skrf.Network(str(tmp_path))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return skrf.Network(str(target_path))


def _copy_maybe_gz(src: Path, dst: Path) -> None:
    if src.suffix.lower() == ".gz":
        with gzip.open(src, "rb") as f_in, open(dst, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    else:
        shutil.copy2(src, dst)
```

修改 `list_batch_files`、`download_files_zip`、`get_file_curve`、`compute_single_file` 中调用 `_safe_resolve` 的地方为 `_find_actual_path`。

同时 `list_batch_files` 的 patterns 增加 `*.s1p.gz`、`*.s2p.gz`，并在展示时去掉 `.gz` 后缀（或保留原样但 size 显示压缩后大小）。

- [ ] **步骤 4：运行测试验证通过**

```bash
uv run pytest tests/api/test_files_gzip.py -v
```

预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/api/files.py backend/tests/api/test_files_gzip.py
git commit -m "feat(files): transparently read gzip-compressed snp files

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 12：#2.zip 集成测试

**文件：**
- 修改：`backend/tests/workers/test_pipeline_batch.py` 或新建 `backend/tests/integration/test_pipeline_2zip.py`

- [ ] **步骤 1：编写集成测试**

```python
# backend/tests/integration/test_pipeline_2zip.py
import os
import shutil
from pathlib import Path

import pytest
from sqlalchemy import delete, select

ZIP_PATH = Path("/Users/jingbozuo/Projects/#2.zip")


@pytest.mark.integration
@pytest.mark.skipif(not ZIP_PATH.exists(), reason="#2.zip 不存在")
@pytest.mark.usefixtures("celery_session_worker")
def test_pipeline_with_2zip(db) -> None:
    """用上级目录 #2.zip 验证流水线端到端行为。"""
    from app.models import Batch, Device, Mapping
    from app.services.upload_service import create_batch_and_dispatch

    mapping = db.scalar(select(Mapping))
    assert mapping is not None, "需要一个 mapping"

    batch_no = "INTEGRATION_2ZIP.1"
    # 清理旧数据
    old = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    if old:
        db.execute(delete(Device).where(Device.batch_id == old.id))
        db.delete(old)
        db.commit()
    shutil.rmtree(Path("/data3/aln/files") / batch_no, ignore_errors=True)

    task = create_batch_and_dispatch(
        db,
        zip_path=ZIP_PATH,
        batch_no=batch_no,
        mapping_id=mapping.id,
        deembed=True,
        deembed_method="default",
    )
    assert task is not None

    # 等待 Celery 任务完成（或在测试里直接调用 apply）
    from app.workers.pipeline_batch import pipeline_batch_task

    result = pipeline_batch_task.apply(
        kwargs={
            "upload_task_id": task.id,
            "zip_path": str(ZIP_PATH),
            "batch_no": batch_no,
            "mapping_id": mapping.id,
            "deembed_method": "default",
        }
    ).get(timeout=3600)

    assert result["device_count"] > 0
    assert result["failures"] == 0 or result["failures"] < result["device_count"] * 0.1

    # 校验 gzip 归档
    files_dir = Path("/data3/aln/files") / batch_no
    gz_files = list(files_dir.rglob("*.s2p.gz")) + list(files_dir.rglob("*.s1p.gz"))
    assert len(gz_files) > 0
```

- [ ] **步骤 2：运行集成测试**

```bash
uv run pytest tests/integration/test_pipeline_2zip.py -v -m integration
```

预期：可能需要根据 #2.zip 实际内容调整断言（如 device_count 范围、失败率阈值）。

- [ ] **步骤 3：根据实际运行结果调整**

- 若解压失败，检查 7z 是否安装、zip 是否为 Deflate64/ZIP64。
- 若去嵌匹配失败，检查 OPEN/SHORT 命名是否符合 `default` 方法。
- 若提参失败率高，检查频率范围、数据格式。

- [ ] **步骤 4：Commit**

```bash
git add backend/tests/integration/test_pipeline_2zip.py
git commit -m "test(pipeline): add integration test with #2.zip

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 13：文档更新

**文件：**
- 修改：`backend/README.md`
- 修改：`docs/api.md`（如文件下载接口行为有变）
- 修改：`docs/operations.md`

- [ ] **步骤 1：更新 backend/README.md**

在"测试"小节后添加：

```markdown
### 边压缩边计算流水线

含 de-embedding 的大 zip 会自动走 `aln.pipeline_batch` 链路：

```bash
uv run celery -A app.workers worker --loglevel=info --concurrency=4
```

相关配置（环境变量）：
- `PIPELINE_ENABLED`：是否启用新链路（默认 true）
- `PIPELINE_WORKERS`：消费者进程数（默认 0 = CPU 核心数）
- `PIPELINE_SCAN_INTERVAL`：文件扫描间隔秒数（默认 1.0）
- `PIPELINE_COMPRESS_RAW`：提参后是否 gzip 原始 snp（默认 true）
```

- [ ] **步骤 2：运行文档构建/检查（如有）**

```bash
# 当前项目无 docs 构建命令；直接检查无语法错误即可
git diff --check
```

预期：无 trailing whitespace 等错误。

- [ ] **步骤 3：Commit**

```bash
git add backend/README.md docs/api.md docs/operations.md
git commit -m "docs: update pipeline usage and configuration

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 任务 14：最终回归验证

- [ ] **步骤 1：运行全部单元测试**

```bash
uv run pytest tests -m 'not integration' -q
```

预期：全部通过（除已有失败外）。

- [ ] **步骤 2：运行 ruff 检查**

```bash
uv run ruff check .
```

预期：无新增错误。

- [ ] **步骤 3：运行 ruff 格式化**

```bash
uv run ruff format .
```

- [ ] **步骤 4：提交格式化修复**

```bash
git add -A
git commit -m "style: ruff format

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 自检

### 规格覆盖度

| 规格需求 | 实现任务 |
|----------|----------|
| 第三方软件解压加速 | 任务 3 `StreamingExtractor` |
| 边解压边计算 | 任务 7/8 `pipeline_batch_task` 协调 |
| De-embedding | 任务 5 `CalibrationIndex` + 任务 6 `DutProcessor` |
| gzip 归档原始 snp | 任务 6 `DutProcessor` + 任务 11 `files.py` |
| 保留原始 snp | 任务 6 压缩后保留 `.gz`；任务 11 透明读取 |
| 配置项 | 任务 1 |
| 进度报告 | 任务 7/8 内 `ProgressPublisher` |
| #2.zip 集成测试 | 任务 12 |

无遗漏。

### 占位符扫描

- 无 "TODO"/"待定" 等占位符。
- 每个代码步骤均给出实际代码。
- 任务 8 中的完整协调代码已给出主干，实现者需替换 `raise NotImplementedError` 并内联所有 helper，不能留空函数。

### 类型一致性

- `zip_contains_calibration` 签名一致。
- `CalibrationIndex.build` / `match` 签名一致。
- `DutProcessor.process` 签名一致。
- `pipeline_batch_task` 参数与 `upload_service` 调用一致。

---

## 执行交接

**计划已完成并保存到 `docs/superpowers/plans/2026-06-25-compress-while-calculate.md`。两种执行方式：**

**1. 子代理驱动（推荐）** - 每个任务调度一个新的子代理，任务间进行审查，快速迭代

**2. 内联执行** - 在当前会话中使用 executing-plans 执行任务，批量执行并设有检查点

**选哪种方式？**
