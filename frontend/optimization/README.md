# ALN 结构寻优离线结果页

独立 P5 页面，直接消费 P4 的完整候选目录；无浏览器代理推理，无外部资源或网络请求。不修改原 `surrogate-html/` 页面。

## 构建

从指定 ML worktree 的 `backend/` 使用本地 uv：

```bash
uv run --no-sync python scripts/build_optimization_p5.py \
  --p4-root surrogate_data/optimization_p4_20261016 \
  --out surrogate_data/optimization_p5_new_build
```

输出目录必须不存在。构建器校验 P4 的交付清单，使用当前仓库 `frontend/node_modules/typescript/bin/tsc` 编译严格 TypeScript；现有 node_modules 已提供，无新增依赖。输出：

- `AlN结构寻优.html`：约 3 MB，可直接双击，所有数据与代码内嵌。
- `browser_data.json`：完整浏览数据包，支持通过页面导入。不是仅含 Top-N 的 P4 `results.json`。
- `build_manifest.json`：输入、源码及输出哈希；构建成功不等于交互测试通过。

可选：附加 `--p10-root surrogate_data/optimization_p10_20261026` 时，构建器先校验 P10 产物清单，再把 D2 异方差参考区间（仅 PF·EG0·700 μm² 子集的 Qp，8 个结构）作为 `d2_reference` 块内嵌进浏览包。该层是实验性回顾证据：页面默认不显示，候选详情中出现开关，仅子集结构有此开关；不嵌入时页面行为与此前完全一致。

可选：附加 `--p11-root surrogate_data/optimization_p11_20261026` 时，构建器校验 P11 产物清单，把外推候选（A/B 组 10 个，C 组演示点按契约排除）作为 `p11_candidates` 块内嵌。页面在 Qp/k² 轴组合下以红色菱形渲染这些点（图例"外推候选（无实测器件）"，含显示/隐藏开关，默认显示），点击显示结构参数、逐目标预测与区间、支持标签与风险标注；外推点不进入排名、比较、Pareto 或导出。不给 `--p11-root` 时浏览包与不含该块的字节完全一致（有回归测试）。

## 交互和契约

实测为默认；探索切换时清空筛选和比较，频率输入禁用。筛选只对 P4 `eligible=true` 的记录进行，不会把排除候选重新准入。重新计算的是当前可行集合的排序及四目标 Pareto，不是新尺寸预测。筛选条件先编辑、再“应用”；无效范围保留已应用结果，空集合明确提示无解。

单目标 Top-N、点图、详情、最多四个结构比较、全筛选集与 Pareto 导出共享结构 ID。Top-N 和“仅 Pareto”仅控制表格显示，导出按钮明确各自范围。JSON 保持原始浮点值；CSV 给字符串公式前缀加单引号，不对数值做界面精度截断。

浏览包格式为 `aln_optimization_browser_v1`，仅支持冻结 `optimization_scalar_p3_v1`。每个目录是列名数组和行数组，保留 P4 字段；导入校验尺寸、ID 唯一性、四目标、模式、坐标数或局部支持、可信标志 false。导入后的来源声明没有独立真实性校验，页面会显示这一限制。不要把导出的筛选 JSON 当浏览包导入。

## 验证

从 ML worktree 的 `frontend/`：

```bash
ALN_P5_BUILD=/absolute/path/to/build \
ALN_P5_TEST_OUTPUT=/absolute/path/to/new-browser-test-directory \
ALN_P5_TEST_REPORT=/absolute/path/to/new-browser-report.json \
node node_modules/@playwright/test/cli.js test --config optimization/playwright.config.ts
```

浏览器使用 `file://` 与离线模式，不启动服务器。测试直接对照 P4 CSV；覆盖排名、Pareto、筛选、空结果、原精度导出、点表联动、比较上限、探索拒绝路径、导入校验/文本安全、键盘操作及 1480/390 像素布局，检查未发生浏览器错误或 HTTP 请求。

P5 的功能验证不是新盲测或可信外推验证。新尺寸仍需独立实测，未恢复 AG 或特殊拓扑，也未提供频率代理。
