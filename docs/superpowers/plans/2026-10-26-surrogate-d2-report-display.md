# D2 异方差参考区间的"仅报告"页面显示实现

2026-10-26。用户选定 P10 的 D2 方案做页面"仅报告"显示后的产品层实施记录。实现落在 P5 结构寻优离线页（唯一已有预测区间显示面）；主仓库平台未改动（理由见下）。

## 链路调查结论（先决事实）

任务要求先调查主仓库产品链路。调查结果：

- 主仓库平台（FastAPI + React 分析工作区）的 ML 仅有曲线谱预测（`backend/app/ml/inference.py`、`sparse/inference.py`，经 `api/devices.py` 提供 PINN/稀疏重建频谱）；**平台不存在标量优化模型的预测区间计算、传输或显示**；`frontend/src/features/analysis/` 只显示实测数据，无任何区间/预测图层。
- P3–P10 的标量优化模型与共形区间全部在 ML worktree；其唯一显示面是 P5 独立离线页（ML worktree `frontend/optimization/` + 构建器 `backend/scripts/build_optimization_p5.py`），该页详情面板已按结构显示实测 bootstrap 区间与 P3 单目标区间。

因此"现有页面附加仅报告层"的真实落点是 P5 页（用户在 P9 遗留项中的原话即"附加到现有页面…只改区间显示"）。把 D2 图层硬编进主仓库分析工作区（一个没有预测区间概念的实测数据视图）不符合任务"不要硬编"的要求；主仓库 backend/frontend 均未改动。文档写在主仓库，实现与证据在 ML worktree。

## 实现范围

- `backend/app/ml/optimization_d2_view.py`（新增）：`verify_p10_products`（按 P10 `outputs_manifest.json` 逐文件 SHA256 校验）与 `make_d2_reference`（从 P10 `experiment1_intervals/small_area_pf_qp.csv` 提取 8 个子集结构的 C0/D2 双口径区间；校验 P10 verification 全通过、恰好 8 结构、子集几何规则 has_pf=1 且 eg=0 且 area=700 与 P4 实测目录一致、区间 0<下界<上界；任何疑点 fail-closed）。
- `backend/scripts/build_optimization_p5.py`（改动）：新增可选 `--p10-root`；给出时构建前校验 P10 清单、内嵌 `d2_reference` 块、构建后复核哈希，`build_manifest.json` 记录 P10 来源与哈希。不给出时产物与此前完全一致（默认行为不变）。
- `frontend/optimization/app.ts`（改动）：`BrowserPackage` 增加可选 `d2_reference`；`validateD2` 严格校验（格式、实验性标志、8 个条目唯一、区间有效、与实测目录逐结构几何交叉核对），导入时同一套校验生效，篡改即整体拒绝且保持原状态；候选详情面板对子集结构渲染开关（默认关闭），勾选后显示参考块；帮助弹窗在有 D2 包时追加说明段。
- `frontend/optimization/style.css`（改动）：新增 `.d2-ref` 虚线琥珀色块样式（沿用既有色板，无新依赖）。
- `frontend/optimization/README.md`（改动）：记录 `--p10-root` 与显示语义。
- `backend/tests/ml/test_optimization_p5_d2.py`（新增）：D2 块提取、8 条目完整性、几何规则、篡改清单校验三个失败路径。

## UI 行为

- 排名、点图、筛选、比较、导出与点值**完全不变**；D2 层不参与任何计算。
- 候选详情：仅当结构属于 PF·EG0·700 μm² 子集（8 个）时出现"显示实验性 D2 参考区间（小面积 PF · 700 μm² · Qp）"开关，默认关闭；勾选后显示：实测典型值、D2 异方差两种联合口径区间、同口径冻结基线 C0 区间、以及限制文案（实验性回顾证据、非冻结配置、非新盲测、仅子集 Qp、内层子集覆盖 14/19、外层回顾性 6/8、trusted=false、新尺寸外推需独立实测、证据文档路径）。
- 开关状态为会话级（切换结构/模式保持），不写入导出；导出内容不含 D2 层。

## 限制

- D2 数值为 P10 产物的只读内嵌导入，页面不计算区间；这正好满足"位置离散度在产品推理时不可获得则不伪造"的要求——disp 由 P9/P10 在 ML worktree 预先算好，页面只展示既有证据。
- D2 未被 P10 冻结选择采纳（选择保留 D0，因其覆盖增益 +21pp 未达 +25pp 阈值）；页面文案明示其为未被采纳的实验性参考。
- 仅覆盖 8 个子集结构的 Qp；其余结构/目标无参考层。两种口径与页面既有 P3 单目标区间口径不同，文案中并列 C0 同口径基线以便比较。
- 未触碰主仓库平台代码；未做 Electron/服务器集成；旧 P5 发布包与桌面副本保留，新构建独立成目录。

## 验证结果

- Ruff：`optimization_d2_view.py`、`build_optimization_p5.py`、`tests/ml/test_optimization_p5_d2.py` 通过。
- 后端：tests/ml 全套 **107 项通过**（含新增 3 项 D2 构建器测试）。
- 前端：构建器内 tsc `--strict` 编译通过；Playwright **7/7 通过**（原 6 组场景在无 D2 块时行为不变 + 新增 D2 场景：开关默认关闭、勾选后数值与内嵌包逐位一致、非子集结构无开关、篡改 `experimental` 或条目数时导入拒绝且原状态不变、无页面异常与网络请求）。证据：`surrogate_data/optimization_p10_20261026/browser_tests.json` 与 `browser_tests/`（含 D2 详情截图）。
- 数值抽查：页面显示 G8-1 D2 scaled [323.8, 583.8] / global [345.8, 546.6]、C0 [432.6, 580.6] / [454.0, 553.2]，与 P10 `small_area_pf_qp.csv` 逐位一致。
- 构建：`surrogate_data/optimization_p10_20261026/viewer_build/`（HTML + browser_data.json + build_manifest.json）；桌面副本 `~/Desktop/AlN结构寻优-P10-D2-20261026.html`。
- 复现：

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-ml/backend
uv run --no-sync python scripts/build_optimization_p5.py \
  --p4-root surrogate_data/optimization_p4_20261016 \
  --p10-root surrogate_data/optimization_p10_20261026 \
  --out surrogate_data/p10_d2_viewer_rebuild
cd ../frontend
ALN_P5_BUILD=/path/to/viewer_build ALN_P5_TEST_OUTPUT=/new/dir ALN_P5_TEST_REPORT=/new/report.json \
  node node_modules/@playwright/test/cli.js test --config optimization/playwright.config.ts
```

## 合规

未改产品算法/标签/冻结制品语义；ML worktree 三个既有 dirty 文件未触碰；主仓库仅新增本文档；无 Git 提交、无部署、无新依赖。主仓库平台的既有状态（`frontend/package-lock.json` dirty）与本轮无关，未触碰。
