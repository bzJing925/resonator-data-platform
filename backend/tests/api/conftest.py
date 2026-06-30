"""API 测试共享配置。

覆盖 DATA_ROOT 到一个可写的临时目录，避免本地默认 /data3 只读导致上传失败。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# 必须在任何测试导入 app.main（从而触发 Settings 加载）之前设置环境变量。
_temp_root = tempfile.mkdtemp(prefix="aln_test_data_")
os.environ["DATA_ROOT"] = _temp_root

for _sub in ("uploads", "files", "mappings", "exports", "logs"):
    Path(_temp_root, _sub).mkdir(parents=True, exist_ok=True)
