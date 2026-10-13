#!/usr/bin/env python
"""一键生成离线单文件代理模型页（默认输出 ~/Desktop/AlN代理模型.html）。

流程：
  1. 运行 export_onnx.py（torch → ONNX，含 torch↔onnxruntime 对齐校验）
  2. 运行 export_data.py（baseline / surr_meta / ui_meta，--with-truth 时含格均值真值）
  3. 从 frontend/node_modules 拷贝 plotly 与 onnxruntime-web 运行时到 vendor/
  4. 读取 template/ 与 assets/，把 ONNX/wasm 二进制 base64 内联、JSON 原样内联、
     plotly/ort/app.js 文本内联，替换 template/index.html 中的 %%TOKEN%% 占位符

用法（必须用 backend 的 venv，导出器依赖其中的 torch/onnx/onnxruntime）：
    backend/.venv/bin/python surrogate-html/build.py [--with-truth] [--out PATH]
                                                     [--skip-export] [--no-check]
"""

from __future__ import annotations

import argparse
import base64
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
NODE_MODULES = REPO_ROOT / "frontend" / "node_modules"
ASSETS = HERE / "assets"
VENDOR = HERE / "vendor"
TEMPLATE = HERE / "template"

# (vendor 文件名, node_modules 来源相对路径, 是否 base64 内联)
VENDOR_FILES = {
    "plotly.min.js": "plotly.js-dist-min/plotly.min.js",
    "ort.wasm.min.js": "onnxruntime-web/dist/ort.wasm.min.js",
    "ort-wasm-simd-threaded.mjs": "onnxruntime-web/dist/ort-wasm-simd-threaded.mjs",
    "ort-wasm-simd-threaded.wasm": "onnxruntime-web/dist/ort-wasm-simd-threaded.wasm",
}

DEFAULT_OUT = Path.home() / "Desktop" / "AlN代理模型.html"


def run_exporters(with_truth: bool, no_check: bool) -> None:
    """用当前解释器（应为 backend/.venv 的 python）依次跑两个导出器。"""
    onnx_cmd = [sys.executable, str(HERE / "export_onnx.py")]
    if no_check:
        onnx_cmd.append("--no-check")
    data_cmd = [sys.executable, str(HERE / "export_data.py")]
    if with_truth:
        data_cmd.append("--with-truth")
    for cmd in (onnx_cmd, data_cmd):
        print("+", " ".join(cmd), flush=True)
        subprocess.run(cmd, check=True)


def copy_vendor() -> None:
    VENDOR.mkdir(parents=True, exist_ok=True)
    for name, rel in VENDOR_FILES.items():
        src = NODE_MODULES / rel
        if not src.is_file():
            raise FileNotFoundError(f"vendor 来源缺失: {src}（先在 frontend/ 下 npm ci）")
        shutil.copy2(src, VENDOR / name)


def b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def inline_json(path: Path) -> str:
    """JSON 文本原样内嵌为 JS 字面量；转义 `</` 防 </script> 提前闭合（`\\/` 是合法 JSON 转义）。"""
    return path.read_text(encoding="utf-8").replace("</", "<\\/")


def build_data_js(with_truth: bool) -> str:
    required = ["surr_meta.json", "baseline.json", "ui_meta.json", "stage1.onnx", "stage2.onnx"]
    missing = [n for n in required if not (ASSETS / n).is_file()]
    if missing:
        raise FileNotFoundError(f"assets 缺失 {missing}，请先运行导出（去掉 --skip-export）")
    parts = [
        '"use strict";',
        f"const SURR_META = {inline_json(ASSETS / 'surr_meta.json')};",
        f"const BASELINE = {inline_json(ASSETS / 'baseline.json')};",
        f"const UI_META = {inline_json(ASSETS / 'ui_meta.json')};",
    ]
    if with_truth:
        cm = ASSETS / "cell_means.json"
        if not cm.is_file():
            raise FileNotFoundError("--with-truth 需要 assets/cell_means.json")
        parts.append(f"const CELL_MEANS = {inline_json(cm)};")
    else:
        parts.append("const CELL_MEANS = null;")
    parts += [
        f'const STAGE1_B64 = "{b64(ASSETS / "stage1.onnx")}";',
        f'const STAGE2_B64 = "{b64(ASSETS / "stage2.onnx")}";',
        f'const ORT_WASM_B64 = "{b64(VENDOR / "ort-wasm-simd-threaded.wasm")}";',
        f'const ORT_GLUE_B64 = "{b64(VENDOR / "ort-wasm-simd-threaded.mjs")}";',
    ]
    return "\n".join(parts)


def assemble(out_path: Path, with_truth: bool) -> int:
    html = (TEMPLATE / "index.html").read_text(encoding="utf-8")
    app_js = (TEMPLATE / "app.js").read_text(encoding="utf-8")
    replacements = {
        "%%PLOTLY_JS%%": (VENDOR / "plotly.min.js").read_text(encoding="utf-8"),
        "%%ORT_JS%%": (VENDOR / "ort.wasm.min.js").read_text(encoding="utf-8"),
        "%%DATA_JS%%": build_data_js(with_truth),
        "%%APP_JS%%": app_js,
    }
    for token, content in replacements.items():
        if token not in html:
            raise RuntimeError(f"template/index.html 缺少占位符 {token}")
        html = html.replace(token, content)
    for token in replacements:
        if token in html:
            raise RuntimeError(f"占位符 {token} 未被完全替换")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path.stat().st_size


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-truth", action="store_true",
                    help="内嵌格均值真值叠加数据（cell_means.json，输出文件约大 8–11MB）")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出 HTML 路径")
    ap.add_argument("--skip-export", action="store_true",
                    help="跳过 torch/数据导出，复用现有 assets/（调模板时用）")
    ap.add_argument("--no-check", action="store_true",
                    help="导出 ONNX 时跳过 torch↔onnxruntime 对齐校验（更快）")
    args = ap.parse_args()

    if not args.skip_export:
        run_exporters(args.with_truth, args.no_check)
    copy_vendor()
    out_path = Path(args.out).expanduser()
    size = assemble(out_path, args.with_truth)
    print(f"已生成 {out_path}（{size / 1024 / 1024:.1f} MB，含真值叠加={args.with_truth}）")


if __name__ == "__main__":
    main()
