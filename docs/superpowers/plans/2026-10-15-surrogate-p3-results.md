# 结构寻优 P0—P3 执行结果

日期：2026-10-15。已执行数据修订、实测排名、基础标量代理和结构/边界留出评估。P4 寻优、P5 页面与 P6 新实测外推未执行；复杂算法由 astra 后续接手。

本阶段达到“可复现、可评价、可交接”，尚未达到“未测结构可可信寻优”。模型包中 `trusted_for_unmeasured_optimization` 明确为 `false`。

依据：[执行计划](/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/docs/superpowers/plans/2026-10-15-surrogate-optimization-execution-plan.md)、[拟合前冻结协议](/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/docs/superpowers/plans/2026-10-15-surrogate-p3-protocol.md)。

## 1. 数据修订结果

| 项目 | 去嵌表 | 未去嵌表 |
| --- | ---: | ---: |
| 原始记录 | 39,818 | 40,800 |
| 真实 ELB002 编号匹配 | 39,818 | 40,800 |
| 常规分支且标签一致 | 26,480 | 27,218 |
| 隔离记录 | 13,338 | 13,582 |
| 其中面积冲突 | 1,320 | 1,319 |

ELB002 排除表头后为 567 个真实编号。修复 K-10 解析漏配、带空格尺寸/面积 token 和表头处理，重复 Mark 会报错。此次没有真正未命中的 DUT。

隔离记录包括 No AG、C>B、BASIC、AG 变体、单面积和缺失尺寸的描述。面积冲突均在这些未解决分支内，不能把原因计数相加当作隔离总量。没有猜测缺失尺寸，没有把缺失 AG 填为 1 或 0，没有按 Fail 字样删除记录。

去嵌主数据形成 **576 个结构、288 个设计组、6 个离散分支**。按 PF、EG 是否为零、FL 是否为零分别建模，不跨零端点混合拓扑。AG 未记录，不是新模型输入。

raw/de 按原始文件名配对，保留端口与 N；共 39,793 对、25 条仅去嵌、1,007 条仅 raw。可靠分支有 26,464 对四目标有效记录。去嵌减 raw 的中位变化：k² −0.689 个百分点、Qs +207.14、Qp +183.35、BodeQ_fitted +211.69。raw 未被混入训练。

详细证据：[数据审计](/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p3_20261015/data/data_audit.json)、[输入版本](/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p3_20261015/data/input_manifest.json)、[规范记录](/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p3_20261015/data/records.csv.gz)、[隔离清单](/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p3_20261015/data/quarantine.csv.gz)。

## 2. 实测结构基线

典型值是“同结构内先按坐标取中位数，再对坐标等权取中位数”。每个结构保留全部有效重复，当前有 12—50 个位置；排名要求至少 10 个位置。表中是本批次已观测范围内的榜首，不是全设计域最优。

| 目标 | EG（μm） | FL（μm） | 面积（μm²） | 几何 PF | 结构典型值 | 位置数 | Mark |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| k² | 2 | 0 | 5,500 | 0 | 14.6745% | 50 | E1-1 |
| Qs | 0 | 2 | 700 | 1 | 1,032.67 | 19 | G8-1 |
| Qp | 1 | 1.5 | 5,500 | 0 | 1,395.96 | 48 | C6-1 |
| BodeQ_fitted | 1 | 1.5 | 5,500 | 0 | 1,409.22 | 48 | C6-1 |

四项分别有完整排名、分位数、位置 bootstrap 区间和单器件高值来源。它们不能拼成一套结构。此次把 Bode Q 暂定为 BodeQ_fitted，其他 Bode Q 口径保留在数据中，没有替代关系。

匹配相同位置后，k² 的榜首与随后四个候选、Qp/Bode Q 的榜首与第二名，差值的描述性区间均跨过 0。宜保留候选组，不宣称唯一冠军；这些区间是筛选后的单批次描述，未做多重比较校正。

按需恢复了 8 个代表器件的 raw s2p 并重提参数：fs/fp 完全一致，k²/Qs/Qp 差异为浮点误差量级，BodeQ_fitted 最大绝对差约 0.00548。原 zip 为 Deflate64，使用项目已安装的 `zipfile_deflate64` 支持，未全量解压。**尚未恢复历史去嵌配方，不能据此宣布去嵌峰值已通过复核。**

证据：[实测榜首与区间](/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p3_20261015/data/empirical_best.json)、[同位置敏感性](/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p3_20261015/peak_review/top_rank_position_sensitivity.csv)、[8 个器件曲线核查](/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p3_20261015/peak_review/peak_curve_review.csv)。

## 3. 模型与独立划分

同一有效结构、同一 mark 及相关双端口通过连接分量组成 design group。划分为 train 340 个结构/170 组、validation 84/42、calibration 84/42、test 68/34；没有结构或双端口跨组泄漏。

固定比较分支中位数、训练范围缩放的最近实测、ExtraTrees（192 棵树、叶子最少 2 个样本、对数目标、种子 20261015）。只按 validation MAE 逐目标选型，四项目标都选择 ExtraTrees。未做 GP、复杂集成或超参数搜索。最终拟合只使用 train + validation 的 424 个结构，校准和测试未加入模型。

| 目标 | 测试 MAE | 相对绝对误差中位数 | R² | 测试候选中 Top-5 命中比例 |
| --- | ---: | ---: | ---: | ---: |
| k² | 0.1328 个百分点 | 0.359% | 0.9865 | 60% |
| Qs | 18.87 | 3.229% | 0.9551 | 80% |
| Qp | 38.82 | 4.354% | 0.9737 | 60% |
| BodeQ_fitted | 32.34 | 2.867% | 0.9606 | 80% |

上表只回答同批次中未参与拟合的设计结构的典型值预测，不代表新晶圆、新工艺或原实测边界之外的保证。Top-5 只在 68 个测试候选内比较。

旧 Stage 1 另作描述性对照：当前可靠结构中 568 个输入出现在旧 dataset，22 个旧输入跨旧 split，训练 raw/de 来源未确认，且没有 BodeQ_fitted 输出。因此没有把旧模型差值当作公平独立测试比较，也没有继承旧模型的精度声明。

证据：[划分清单](/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p3_20261015/evaluation/split_manifest.csv)、[全部候选验证分数](/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p3_20261015/evaluation/validation_metrics.csv)、[测试结果](/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p3_20261015/evaluation/evaluation_summary.json)、[旧模型诊断](/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p3_20261015/evaluation/legacy_diagnostic.json)。

## 4. 区间与边界验证暴露的问题

区间采用 calibration design group 内最大绝对对数残差，名义覆盖率 90%。它预测结构典型值，不是单器件区间；有限样本和同批次位置相关性仍是限制。

| 目标 | 随机结构留出：结构覆盖 | 随机结构留出：整设计组覆盖 |
| --- | ---: | ---: |
| k² | 91.2% | 85.3% |
| Qs | 95.6% | 91.2% |
| Qp | 89.7% | 79.4% |
| BodeQ_fitted | 98.5% | 97.1% |

整组覆盖并未稳定达到名义水平，不能把“90%”当作当前已兑现的逐分支可靠性。

预先冻结了 FL 上边界、EG 上边界、面积两端留出；相应设计组及关联端口不进入训练。以下为固定 ExtraTrees 的描述性结果，未据此调参：

| 留出方向 | 目标 | 测试结构数 | MAE | 区间结构覆盖 |
| --- | --- | ---: | ---: | ---: |
| FL=2，训练只见更小 FL | BodeQ_fitted | 72 | 71.93 | **55.6%** |
| FL=2，训练只见更小 FL | Qp | 72 | 63.96 | 100.0% |
| EG=2，训练只见更小 EG | Qp | 96 | 41.35 | 97.9% |
| 面积 700/5500，训练只见中间档 | k² | 96 | 0.4056 个百分点 | **64.6%** |
| 面积 700/5500，训练只见中间档 | Qs | 96 | 38.44 | 82.3% |

结论是基础拟合可用作后续比较，但边界不确定性仍未解决。此次没有获得新结构盲测，也没有工程确认的误差预算，因此未开放可信未测结构推荐。

证据：[边界分数](/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p3_20261015/evaluation/boundary_metrics.csv)、[模型口径与限制](/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p3_20261015/evaluation/model_metadata.json)。

## 5. 可运行入口与交付位置

ML 源码均位于 `/Users/jingbozuo/Projects/aln-data-backend-ml`，尚未合入 main：

- [数据处理](/Users/jingbozuo/Projects/aln-data-backend-ml/backend/app/ml/optimization_data.py)：审计、隔离、配对、结构聚合与实测排名。
- [基础代理与评价](/Users/jingbozuo/Projects/aln-data-backend-ml/backend/app/ml/optimization_baselines.py)：分组、固定模型、区间和支持范围。
- [主命令行](/Users/jingbozuo/Projects/aln-data-backend-ml/backend/scripts/run_optimization_p3.py)：prepare / evaluate / predict。
- [高值核查](/Users/jingbozuo/Projects/aln-data-backend-ml/backend/scripts/audit_optimization_peaks.py)、[真实产物核验](/Users/jingbozuo/Projects/aln-data-backend-ml/backend/scripts/verify_optimization_p3.py)。

持久产物根目录：`/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p3_20261015/`。

| 子目录 | 内容 |
| --- | --- |
| data | 输入哈希、冻结协议、全部规范记录、隔离清单、结构表、覆盖表、四目标排名、raw/de 配对 |
| evaluation | 划分、选型、逐结构预测、分支/边界分数、旧模型诊断、joblib 模型与元数据 |
| peak_review | 位置配对检查、8 个器件原始曲线及 raw 重提对照 |
| receipts | CLI 输入输出、独立核验、检查记录和任务源码快照 |

`peak_review_failed_deflate64` 是首次未注册解码器时留下的不完整尝试，不是验收产物；完整结果使用 `peak_review`。

模型只预测四项标量；AG、fs/fp、复数曲线不是新模型输出。后续 P4 若需要频率约束，应先补充并验证频率预测，不能把旧模型频率与新模型 Q 默认为同一条响应。模型为本机 sklearn/joblib 包，尚未验证 ONNX/浏览器导出。

已有目录拒绝覆盖。查看已训练模型的批量预测：

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-ml/backend
uv run --no-sync python scripts/run_optimization_p3.py predict \
  --bundle surrogate_data/optimization_p3_20261015/evaluation/scalar_baselines.joblib \
  --input surrogate_data/optimization_p3_20261015/receipts/prediction_inputs.csv \
  --out /tmp/aln-p3-predictions-new.csv
```

输入列为 `eg_um,fl_um,area_um2,has_pf`。显式 AG 或未知 cohort 拒绝；未知分支返回缺失预测和状态，范围外输入标为探索。所有输出都保留 `trusted_for_unmeasured_optimization=false`。

## 6. 复现与验收

在当前已就绪的项目 uv 环境执行，替换为新的输出目录：

```bash
cd /Users/jingbozuo/Projects/aln-data-backend-ml/backend
P3_REPLAY_DIR="$(mktemp -d /tmp/aln-p3-replay-XXXXXX)"
uv run --no-sync python scripts/run_optimization_p3.py prepare \
  --de '/Users/jingbozuo/00.PROJECTS/ALN-DATA/data/#2_de_results.xlsx' \
  --raw '/Users/jingbozuo/00.PROJECTS/ALN-DATA/data/#2_results.xlsx' \
  --mapping /Users/jingbozuo/.aln-data/mappings/2_ELB002.xlsx \
  --protocol /Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/docs/superpowers/plans/2026-10-15-surrogate-p3-protocol.md \
  --zip '/Users/jingbozuo/00.PROJECTS/ALN-DATA/data/#2.zip' \
  --out "$P3_REPLAY_DIR/data"
uv run --no-sync python scripts/run_optimization_p3.py evaluate \
  --data-dir "$P3_REPLAY_DIR/data" --out "$P3_REPLAY_DIR/evaluation" \
  --legacy-root /Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master
uv run --no-sync python scripts/audit_optimization_peaks.py \
  --data-dir "$P3_REPLAY_DIR/data" --zip '/Users/jingbozuo/00.PROJECTS/ALN-DATA/data/#2.zip' \
  --out "$P3_REPLAY_DIR/peak_review"
mkdir "$P3_REPLAY_DIR/receipts"
cp surrogate_data/optimization_p3_20261015/receipts/prediction_inputs.csv "$P3_REPLAY_DIR/receipts/"
uv run --no-sync python scripts/run_optimization_p3.py predict \
  --bundle "$P3_REPLAY_DIR/evaluation/scalar_baselines.joblib" \
  --input "$P3_REPLAY_DIR/receipts/prediction_inputs.csv" --out "$P3_REPLAY_DIR/receipts/prediction_outputs.csv"
uv run --no-sync python scripts/verify_optimization_p3.py --run-dir "$P3_REPLAY_DIR"
```

已实际运行：针对本任务的 Ruff 检查通过；文件名、映射、提参、Touchstone、代理提取和新增 P3 测试共 **74 项通过**。真实产物核验独立复算了测试 MAE、校准分位数、划分隔离、CLI 跨进程一致性与源文件哈希；3 个留出结构和 2 个不支持输入共 20 条输出检查通过。

核验收据：[verification.json](/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p3_20261015/receipts/verification.json)。

未改原始 xlsx、个人数据库、原有 HTML 或已发布模型；原 zip 仅检查目录并按需读取 8 个源器件，未做整个 9.8 GiB 文件的内容校验。保留 ML worktree 原有训练脚本修改和实验目录，没有提交或整体清理其他工作。

## 7. 方法背景与后续入口

ExtraTrees 参数含义以 [scikit-learn 官方文档](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.ExtraTreesRegressor.html) 为参考；本机执行版本为 1.9.0，完整版本见输入 manifest。Conformal 方法参考 [Angelopoulos 与 Bates 的教程](https://arxiv.org/abs/2107.07511)；本次将校准单位定义为设计组，其交换性与边界分布变化限制须由后续验证检验。

复杂算法建议先解决边界校准、误差预算和曲线/标量口径，再决定是否引入 GP、物理校准、多保真或贝叶斯优化。详细外推路线沿用 [有限外推计划](/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/docs/superpowers/plans/2026-10-12-baw-sim-to-real-extrapolation-plan.md)。现有 test 和边界结果已被本轮查看，未来按它们改模型时不能继续称为新的盲测。
