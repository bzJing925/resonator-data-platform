# P12：细化与扩大的 Qp/k² 外推搜索结果

2026-10-26。结论：**细化搜索找到 1 个点预测超基线的候选——P1203（EG=3.0, FL=0.025, 5500 μm², PF=0）的 k² 在 P6C trend_gp 下为 15.640%，高于 E1-1 的 14.674%（+0.965 个百分点）；但这是单模型结果（P3 ExtraTrees 同点仅 14.454）、位于搜索域边界（EG 上限）且超出实测范围，属待实测验证假设。** Qp 方向两模型均无超基线峰。P11 结论（粗网格无超基线候选）被细化搜索部分修正：粗网格确实漏掉了 GP 的 k² 峰。

依据：[P12 冻结协议](2026-10-26-surrogate-p12-protocol.md)（运行前保存，含三条实现修订记录）。工作目录 `/Users/jingbozuo/Projects/aln-data-backend-ml/backend`；产物 `surrogate_data/optimization_p12_20261026/`。

## 搜索空间与方法

细网格：PF=0、area=5500、EG ∈ [0, 3.0] × FL ∈ [0, 2.0] 步长 0.025（9801 点，P11 网格为其子集；剔除与实测目录精确重合的点后才取峰）。双模型并行：P3 ExtraTrees 包（含区间与支持标签）与 P6C trend_gp_log_area（structure_holdout 折保存模型）。每模型×每目标网格 argmax；GP 光滑面另做 Nelder-Mead 三起点精化（仅作峰位确认记录）。联合代表取模型内 (Qp, k²) Pareto 非支配点中距实测前沿最近者（同值平台去重）。EG 延长线（3.25–4.0）与三个负面对照（EG=3.5/FL=0、FL=2.5、700-PF）只评估不作候选。

## 是否找到超基线候选

逐口径回答（基线：Qp 1395.96 / k² 14.6745%）：

- **点预测**：k² 有 1 个超基线候选（仅 GP）；Qp 无；P3（冻结产品模型）两目标均无超基线候选。
- **区间下界**：无任何候选的 P3 区间下界超基线。
- **区间上界**：多个候选上界超过基线（如 P1200 Qp 上界 1540.7、P1202 k² 上界 15.16）——仅表达上行可能，不是预测改善。

峰位搜索记录（`peak_searches.csv`）：

| 目标 | 模型 | 峰位（EG, FL） | 峰值 | 超基线 |
|---|---|---|---:|---|
| Qp | P3 | (0.975, 1.5) | 1367.0 | 否 |
| Qp | P6C GP | (3.0, 2.0)（搜索域角落） | 1395.3 | 否（< 1395.96） |
| k² | P3 | (1.3, 0) | 14.658 | 否 |
| k² | P6C GP | (3.0, 0.025) | **15.640** | **是（+0.965 pct）** |

GP 精化（三起点收敛 15.663/15.663/15.662）确认 k² 峰真实存在于 GP 面；精化上确界位于分支边界（FL→0⁺），按协议修订记录不作为候选几何，候选取网格点 (3.0, 0.025)。

## Top 候选与 P11 差异

候选集合 = 6 个 P12 峰位/联合候选 + 10 个 P11 A/B 保留对照 + 3 个负面对照（不推荐，不进图）。P12 新候选：

| 候选 | EG | FL | 来源 | Qp 点预测 [P3 区间] | k² 点预测 [P3 区间] | k² P6C 对照 | 支持标签 |
|---|---:|---:|---|---|---|---:|---|
| P1200 | 0.975 | 1.5 | Qp P3 峰（冠军邻点） | 1367.0 [1213.2, 1540.7] | 14.019 [13.551, 14.504] | 14.020 | exact_training_structure 邻域 |
| P1201 | 3.0 | 2.0 | Qp GP 峰（双边界角落） | 1183.8 [1050.5, 1334.0] | 13.753 [13.294, 14.229] | 14.064 | outside_training_bounds |
| P1202 | 1.3 | 0 | k² P3 峰 | 1093.1 [970.1, 1231.9] | 14.658 [14.169, 15.164] | 14.665 | within_hull_new_level |
| P1203 | 3.0 | 0.025 | **k² GP 峰** | 1205.0 [1069.3, 1358.0] | 14.454 [13.971, 14.953] | **15.640** | outside_training_bounds |
| P1204 | 1.25 | 0.175 | 联合 P3 代表 | 1208.4 [1072.3, 1361.7] | 14.452 [13.970, 14.951] | 14.779 | outside_training_bounds |
| P1205 | 1.275 | 0.275 | 联合 P3 代表 | 1206.6 [1070.8, 1359.8] | 14.452 [13.970, 14.952] | 14.670 | outside_training_bounds |

与 P11 的差异：P11 粗网格的 A 组最近点是 (3.0, 0)/(3.0, 0.5)，其 GP k² 分别为 14.61/14.45——粗网格漏掉了 GP 在 (3.0, 0.025) 的 15.64 峰。P11 的 B 组与 A 组全部保留为对照，排名与预测见 `candidates.csv`/`rankings.csv`。

## 外推风险与适用边界

- P1203 的关键限制：k² 超基线**只有 GP 一个模型**预测（P3 同点 14.454 低于基线）；峰位在 EG 搜索上限与 fl1 分支下缘附近；EG 延长评估显示 fl1 分支的 GP k² 趋势持续到 EG=4.0（15.83），fl0 分支则回落——两分支行为相反，说明该峰是分支内趋势外推，未经实测前可信度低。仿真方向一致（EG↑→k²↑，含 P6B 反例记录）。
- P1201 是双边界角落（EG=3.0 且 FL=2.0）的 GP Qp 峰，两模型在该点严重分歧（P3 1183.8 vs GP 1395.3），风险标注已含角落警告。
- Qp 无任何超基线证据；负面对照（EG=3.5、FL=2.5、700-PF）全部不显示上行且保留记录。
- 所有候选均为计算网格点，制造需版图/工艺确认；均为待实测验证假设。

## 证伪方案更新

`falsification_plan.md` 已更新为 P12 候选集合。最小集合建议：P1203（k² 峰，EG=3.0/FL=0.025）+ P1200（Qp 冠军邻点对照）+ P11 中保守下界最接近前沿者；通过判定沿用基线阈值（Qp 1395.96 / k² 14.6745%），C6-1/E1-1 同批复测对照。EG>3 的更深外推在 EG=3.0 点验证前不建议制造。

## HTML 视图更新

候选集合变化（10 → 16 个可显示点）：构建器新增 `--p12-root`，内嵌 `p12_candidates` 块（schema `aln_optimization_p12_candidates_v1`，自带 n_entries 校验）；页面同时兼容旧 p11 块（两者同在以 P12 为准）。红点渲染、点击详情、开关、"待实测验证"文案口径不变；详情新增来源标识（P12 峰位来源/P11 保留对照）。新桌面 HTML：`~/Desktop/AlN结构寻优-P12-外推候选-20261026.html`。不给 `--p12-root` 时构建产物与 P11 构建字节一致（回归测试通过）。

## 复现与验证

新增源码：`app/ml/optimization_p12.py`、`app/ml/optimization_p12_view.py`、`scripts/run_optimization_p12.py`、`scripts/verify_optimization_p12.py`、`tests/ml/test_optimization_p12.py`；改动 `scripts/build_optimization_p5.py`、`frontend/optimization/app.ts`、`viewer.spec.ts`、`README.md`（P12 参数说明）。

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-ml/backend
uv run --no-sync python scripts/run_optimization_p12.py \
  --p3-root surrogate_data/optimization_p3_20261015 \
  --p4-root surrogate_data/optimization_p4_20261016 \
  --p6c-root surrogate_data/optimization_p6c_20261022 \
  --p11-root surrogate_data/optimization_p11_20261026 \
  --protocol /Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/docs/superpowers/plans/2026-10-26-surrogate-p12-protocol.md \
  --out surrogate_data/p12_reproduce_new
uv run --no-sync python scripts/verify_optimization_p12.py \
  --root surrogate_data/p12_reproduce_new --p4-root surrogate_data/optimization_p4_20261016 \
  --p11-root surrogate_data/optimization_p11_20261026 --out surrogate_data/p12_reproduce_new/verification.json
uv run --no-sync python scripts/build_optimization_p5.py \
  --p4-root surrogate_data/optimization_p4_20261016 \
  --p10-root surrogate_data/optimization_p10_20261026 \
  --p12-root surrogate_data/optimization_p12_20261026 \
  --out surrogate_data/p12_viewer_rebuild
```

验证：Ruff 全部通过；tests/ml 全套 **118 项通过**（含新增 4 项 P12 测试与 1 项无参构建字节回归）；独立核验 **9/9**（源码不变、网格与冻结定义一致、候选不与实测目录重合、峰位从网格重算一致、P11 保留值 1e-9 一致、负面对照标记且不进图、排名一致、证伪方案覆盖、清单完整）；完整重跑数值列 1e-9 容差一致、其余字节一致。Playwright **8/8 通过**（新构建上全部回归 + 泛化红点场景：16 个红点与块一致、点击详情逐位一致、篡改拒绝），截图证据在 `browser_tests/`。未改产品代码/标签/划分/冻结制品；既有 dirty 文件未触碰；无 Git 提交、无部署、无新依赖、未跑 COMSOL。
