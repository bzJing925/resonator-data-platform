from __future__ import annotations

import logging
import zipfile
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
