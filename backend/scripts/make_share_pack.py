"""同事侧分享包制作 CLI：snp zip/散文件 → 参数分享包（MB 级，可传 GitHub）。

与平台摄取管线完全同源（DutProcessor + extract_resonator_params），
输出仅含提取参数（不含原始 snp）——静态网站只展示参数，不丢任何网页可见信息。

用法（在 backend/ 目录下）：
    uv run python scripts/make_share_pack.py 数据.zip --batch-no '#5' \
        --mapping ../mappings/1_ELB003.xlsx -o 分享包_5.zip
    # 多 zip / 散文件混合、去嵌：
    uv run python scripts/make_share_pack.py a.zip b.zip --batch-no '#5' \
        --mapping m.xlsx -o out.zip --deembed --deembed-method gsg100

核心逻辑在 app/share_pack.py（本文件仅 CLI 包装）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.share_pack import PACK_COLUMNS, make_share_pack  # noqa: F401, E402


def main() -> None:
    ap = argparse.ArgumentParser(description="snp zip/散文件 → 参数分享包")
    ap.add_argument("inputs", nargs="+", help="snp 压缩包或散文件路径（可多个）")
    ap.add_argument("--batch-no", required=True, help="批次号（如 '#5'，网站上必须唯一）")
    ap.add_argument("--mapping", required=True, help="对照表 xlsx/csv 路径")
    ap.add_argument("-o", "--out", required=True, help="输出分享包路径（.zip）")
    ap.add_argument("--f-start", type=float, default=None, help="频率下限 GHz（可选，仅记录）")
    ap.add_argument("--f-end", type=float, default=None, help="频率上限 GHz（可选，仅记录）")
    ap.add_argument("--deembed", action="store_true", help="去嵌（zip 需含 OPEN/SHORT 校准 s2p）")
    ap.add_argument("--deembed-method", default="default",
                    choices=["default", "original", "gsg100", "vz", "basic"])
    args = ap.parse_args()
    cb = lambda stage, cur, tot, msg: print(f"[{stage}] {cur}/{tot} {msg}")  # noqa: E731
    make_share_pack(
        args.inputs, args.batch_no, args.mapping, args.out,
        f_start_ghz=args.f_start, f_end_ghz=args.f_end,
        deembed=args.deembed, deembed_method=args.deembed_method,
        progress_cb=cb,
    )


if __name__ == "__main__":
    main()
