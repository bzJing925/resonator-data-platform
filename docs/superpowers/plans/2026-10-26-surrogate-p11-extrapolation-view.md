# P11 外推候选红点视图实现说明

2026-10-26。用户需求："将不存在的器件结构外推得到的器件性能（Qp、k2）补充到 html 网页中，外推所得用红色点标出，点击该点显示对应的结构参数。"目标页面为 P5 结构寻优离线 HTML 体系（ML worktree `frontend/optimization/` + 构建器 `backend/scripts/build_optimization_p5.py`）。用户明确授权本轮扩展该页（覆盖 P11 时"不扩展"的决定）。

## 实现要点

- **数据源**：`surrogate_data/optimization_p11_20261026/` 的 `candidates.csv`（A/B 组 10 个候选；C 组演示点按契约排除，构建时断言其存在但不入块）与 `verification.json`（必须全通过）。新增 `backend/app/ml/optimization_p11_view.py`：`verify_p11_products`（产物清单 SHA256 逐文件校验）与 `make_p11_candidates`（提取、有限性/区间顺序/目录撞 ID 校验，任何疑点 fail-closed）。
- **构建器**：新增可选 `--p11-root`；构建前后校验 P10/P11 清单哈希；`build_manifest.json` 记录来源。不给 `--p11-root` 时浏览包与 D2-only 构建字节一致（回归测试 `test_package_without_p11_root_is_byte_identical_to_d2_only_build` 实测通过）。
- **页面**：`BrowserPackage` 增加可选 `p11_candidates`；`validateP11` 严格校验（格式、实验性标志、恰好 10 个 A/B 条目、ID 不与实测/探索目录冲突、区间有效），导入时同一套校验，篡改即整体拒绝且保持原状态。

## 红点与点击行为

- 散点图 X/Y 轴均为 Qp/k² 时（默认视图即 k²×Qp），候选以**红色菱形**渲染，图例"◆ 外推候选（无实测器件）"；轴切到 Qs/Bode Q 时红点是隐藏的（候选只有 Qp/k² 预测），图注提示"外推候选仅有 Qp/k² 预测"。"外推点"开关默认显示，可隐藏。
- 点击红点：详情面板切到候选视图——候选 ID、组/推荐级别、结构参数（EG/FL/面积/PF/分支）、逐目标预测点值与 P3 区间、P6C 敏感性对照值、支持标签（如 outside_training_bounds）、外推轴与距离、相对 C6-1/E1-1 基线的差值、风险标注全文，以及"待实测验证假设，非已证实更优；Qp 无仿真先验；外推区区间不可靠（700-PF 0/8 失败史）"提示与证据文档路径；"返回结构详情"按钮回到目录视图。
- 外推点不进入排名、Pareto、比较、筛选与任何导出；实测目录行为完全不变。

## 验证结果

- Ruff：`optimization_p11_view.py`、`build_optimization_p5.py`、`tests/ml/test_optimization_p5_p11.py` 通过。
- 后端：tests/ml 全套 **113 项通过**（含新增 3 项：A/B 提取与 C 排除、篡改 fail-closed、无参构建字节一致）。
- 前端：构建器内 tsc `--strict` 通过；Playwright **8/8 通过**（原 7 组回归 + 新场景：红点数量与候选清单一致（10）、点击详情的结构参数与预测值和内嵌包逐位一致、图例/开关/轴切换行为、关闭返回、篡改拒绝、实测计数不变），含截图 `browser_tests/.../p11-candidate-detail.png`。
- 证据目录：`surrogate_data/optimization_p11_20261026/viewer_build/`（HTML + browser_data.json + build_manifest.json）与 `browser_tests/`、`browser_tests.json`。

## 产物路径

- 桌面副本：`~/Desktop/AlN结构寻优-P11-外推候选-20261026.html`（双击即用，离线）。
- 复现：

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-ml/backend
uv run --no-sync python scripts/build_optimization_p5.py \
  --p4-root surrogate_data/optimization_p4_20261016 \
  --p10-root surrogate_data/optimization_p10_20261026 \
  --p11-root surrogate_data/optimization_p11_20261026 \
  --out surrogate_data/p11_viewer_rebuild
```

## 合规

未改主平台代码/标签/划分/冻结制品；D2 层与全部既有页面行为不变；ML worktree 三个既有 dirty 文件未触碰；主仓库仅新增本文档；无 Git 提交、无部署、无新依赖。候选仍为待实测验证假设——页内文案与 P11 结果文档口径一致。
