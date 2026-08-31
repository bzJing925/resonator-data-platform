#!/usr/bin/env python3
"""静态网页版端到端验证（规格 §5.3/§9）。

前置：frontend/dist 已由 build_web.py --no-deploy 产出；本地起静态服务：
    cd frontend/dist && python3 -m http.server 8642

断言：
1. 仪表盘统计显示非零器件数
2. 数据分析页查询返回行
3. 代理模型页预测谱与黄金夹具（Python SurrogatePredictor）逐点 |diff| < 0.05 dB
4. 批次列表含 #2，详情器件表有行
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
BASE = "http://localhost:8642"
GOLDEN = ROOT / "frontend/src/ml/__fixtures__/surrogate.json"


def main() -> None:
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")
        if not ok:
            failures.append(name)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # 1. 仪表盘
        page.goto(f"{BASE}/#/")
        page.wait_for_timeout(2500)
        body = page.inner_text("body")
        check("仪表盘显示器件数", "50,411" in body or "50411" in body,
              "（找 50,411）")

        # 2. 数据分析页：DuckDB 查询返回行
        page.goto(f"{BASE}/#/explore")
        page.wait_for_timeout(1000)
        page.click("text=查询")
        page.wait_for_timeout(6000)  # 首查含 wasm 下载
        body = page.inner_text("body")
        has_rows = "行" in body and "暂无" not in body
        check("数据分析页查询出数", has_rows)

        # 3. 代理模型页：与黄金夹具对拍
        golden = json.loads(GOLDEN.read_text())
        case = next(
            (c for c in golden["cases"]
             if c["eg"] == 1.0 and c["fl"] == 0.5 and c["area"] == 1500.0 and c["pf"] == 0.0),
            golden["cases"][0],
        )
        page.goto(f"{BASE}/#/surrogate")
        page.wait_for_selector("text=代理模型", timeout=15000)
        # 填输入
        page.fill('input[type="number"] >> nth=0', str(case["eg"]))
        page.fill('input[type="number"] >> nth=1', str(case["fl"]))
        # area 选择器
        page.select_option("select", str(int(case["area"])))
        page.wait_for_timeout(12000)  # wasm 下载 + 两次推理 + 去抖
        # 从页面取预测曲线：重放推理（evaluate 直接调 dist 里的模块不可行，
        # 改为读 Plotly 图的 trace 数据）
        z_db = page.evaluate(
            """() => {
              const gd = document.querySelector('.js-plotly-plot');
              if (!gd || !gd.data) return null;
              const tr = gd.data.find((t) => t.name === '代理模型');
              return tr ? Array.from(tr.y) : null;
            }"""
        )
        if z_db is None:
            check("代理模型页出图", False, "未找到 Plotly trace")
        else:
            diffs = [abs(a - b) for a, b in zip(z_db, case["z_db"])]
            mx = max(diffs)
            check("代理模型谱对拍 <0.05dB", len(z_db) == len(case["z_db"]) and mx < 0.05,
                  f"max|diff|={mx:.5f} dB, n={len(z_db)}")

        # 4. 批次列表 + 详情
        page.goto(f"{BASE}/#/batches")
        page.wait_for_timeout(2000)
        body = page.inner_text("body")
        check("批次列表含 #2", "#2" in body)
        page.click("text=#2")
        page.wait_for_timeout(6000)  # 器件表首查含 wasm 下载
        body = page.inner_text("body")
        check("批次详情器件表有行", "器件列表" in body and "暂无器件" not in body)

        check("无页面 JS 异常", not errors, "; ".join(errors[:3]))
        browser.close()

    if failures:
        print(f"\n{len(failures)} 项失败: {failures}")
        sys.exit(1)
    print("\n全部端到端断言通过")


if __name__ == "__main__":
    main()
