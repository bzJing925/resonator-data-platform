# P5 离线结果页面交付

日期：2026-10-16。用户“继续”后，完成受限 P4 的离线页面及浏览器交互验收；没有重训、更新划分或开放可信外推。

## 可直接使用

- 桌面单文件：`/Users/jingbozuo/Desktop/AlN结构寻优-P5-20261016.html`。
- 原始发布目录：`/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p5_20261016/release/`。
- 打包文件：同一 P5 根目录的 `AlN结构寻优-P5-20261016.zip`，包含 HTML、完整浏览数据包、使用说明、构建哈希清单。
- [P4 计算结果与复现命令](2026-10-16-surrogate-p4-results.md)、[P5 实施边界](2026-10-16-surrogate-p5-plan.md)。

直接双击 HTML 打开，无需启动服务器或 Python。默认是 576 个实测结构，四目标 Pareto 为 125 个；探索目录内嵌 2,934 个计算点，其中 1,436 个可参与探索性筛选。页面所有代理结果仍保持未测结构不可信标记。

## 使用顺序

1. 选择“实测结构”或“代理探索”。两个模式的数据与标记分开；切换时清空条件及比较选择。
2. 填写指标下限、EG/FL/面积范围、PF，然后点击“应用筛选”。实测模式还支持 fs/fp 典型频率窗口。修改但未应用的条件不会影响当前显示或导出。
3. 切换排名目标，查看 Top-N 或当前四目标 Pareto；点击散点或结构名查看该结构的全部四指标与区间。
4. 勾选最多四个结构比较。图、表、详情、比较和导出共享同一结构 ID。
5. 导出完整筛选集 CSV/JSON、当前 Pareto CSV，或原目录的排除记录。界面显示精度不改变计算与导出精度。Top-N 和“仅 Pareto”是表格显示选项，完整筛选集导出不受其限制。

无解时显示空结果及“约束未自动放宽”；非法上下限不更新当前结果。探索模式无频率模型，频率输入禁用。页面明确其工作是筛选已经计算的目录，不在浏览器生成新尺寸预测。

## 已验证的功能

最终 6 组 Playwright 场景全部通过，耗时 3.6 秒；采用 Chromium headless，`file://` 与浏览器离线模式，没有服务器或 API mock。6 组内含多项断言：

- 两个模式的四项 Top-10 ID 与 P4 一致；576 个实测结构、125 个 Pareto ID 与 P4 一致。
- 四指标导出值按绝对容差 10⁻⁹ 对照 P4 CSV；保持同一结构完整向量。
- 原 P4 可行约束示例得到相同 3 个结构；k²≥99% 返回空集；倒置面积范围保持之前结果。
- 实际点击散点、表格与详情联动，四结构比较正确；第五个比较项拒绝，不越过上限；筛选移除不可行的比较选择。
- 探索输出的 1,436 个可行 ID 与 P4 一致；1,498 条预先排除记录全部保留，未允许重新准入。
- 浏览数据包导入、非法可信标志、重复 ID、HTML 文本和 CSV 公式前缀处理通过；失败导入不替换原状态。
- 桌面 1480×1000、窄屏 390×844 的实际浏览器截图已检查。页面没有横向溢出；宽表在自身容器内横向滚动。帮助弹窗和表格键盘操作通过。
- 每个场景检查无页面异常、无 HTTP(S) 请求。源码 CSP 同时禁止网络连接和外部资源。

最终证据在 `browser_tests_release.json`、`browser_tests_release/`；第一轮 `browser_tests.json` 和 `browser_tests/` 保留。第一轮 5 组通过、1 组测试超时，原因是测试使用 `.check()` 要求第五个比较项成功选中，而产品规则应拒绝；改为点击并断言未选中后通过，没有为测试放宽产品规则。

另有 P3/P4 的 27 项 ML 回归测试通过；新构建器 Ruff、TypeScript 严格编译、两工作区 `git diff --check` 通过。构建器拒绝覆盖现有发布目录的路径也已实际检查。证据见 `receipts/checks.json`。

## 源码与复现

所有新增源码位于指定 ML worktree：

- `backend/scripts/build_optimization_p5.py`：校验 P4 清单、打包完整目录、编译 TypeScript、构建 HTML、记录哈希。
- `frontend/optimization/app.ts`、`index.html`、`style.css`：单文件页面源。
- `frontend/optimization/playwright.config.ts`、`viewer.spec.ts`：独立浏览器验收。
- `frontend/optimization/README.md`：构建与契约说明。

构建：

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-ml/backend
uv run --no-sync python scripts/build_optimization_p5.py \
  --p4-root surrogate_data/optimization_p4_20261016 \
  --out surrogate_data/optimization_p5_reproduced
```

浏览器测试，使用新的证据路径以保留先前结果：

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-ml/frontend
ALN_P5_BUILD=/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p5_reproduced \
ALN_P5_TEST_OUTPUT=/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p5_browser_reproduced \
ALN_P5_TEST_REPORT=/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p5_browser_reproduced.json \
node node_modules/@playwright/test/cli.js test --config optimization/playwright.config.ts
```

构建约 3 MB HTML，依赖已有本地 uv、Node、TypeScript；不新增库。运行页面无需这些环境。发布目录包含 `browser_data.json`；导入必须是这个完整包，不能用只含 Top-N 的 P4 `results.json` 或筛选结果 JSON 代替。导入的来源真实性未独立校验，页面会明确标注。

## 保全与下一阶段

P3、P4 产物，以及本轮保护记录内的旧代理模型页面源码与已有桌面 HTML 均按 SHA256 核对不变。原 dirty 文件未清理，旧模型未被替换。当前主工作区只新增 P5 文档；无提交、平台集成、服务器部署或数据库改动。

`receipts/delivery_manifest.json` 记录发布 HTML、桌面副本、ZIP、源码、文档和验收产物哈希；`receipts/task_source_snapshot.tar.gz` 是本轮源码覆盖包，不是独立开发环境。`build_v1/` 为初版，正式交付以 `release/` 和最终验收报告为准。

本次完成受限 P5 功能闭环，不声称可信未测寻优或 M2 完成。尚未验证 Safari、Edge、Windows 浏览器、平台内集成，也未开展新的物理测量。P3 已见 test 和边界集继续只能作回顾性对照。

继续算法工作时应优先确认目标频带、制造约束、工程容差及去嵌流程，针对面积/FL 边界失效建立新的冻结实验。缺少实际 AG/特殊拓扑/面积冲突证据的记录继续隔离。新尺寸外推最终仍需独立实测；增加模型复杂度或改页面不能代替这些证据。
