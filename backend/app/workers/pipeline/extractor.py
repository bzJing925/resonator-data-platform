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
