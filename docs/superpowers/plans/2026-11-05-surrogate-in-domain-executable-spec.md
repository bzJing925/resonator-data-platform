# 数据范围内代理模型训练：可执行规格 v1.0

计划 ID：`SURROGATE_IN_DOMAIN_V1`。日期：2026-11-05。

**状态：PLANNED / AWAITING_EXECUTOR_READBACK。本文不是训练完成证明。**

本轮目标是用已有实测标签完成范围内训练、验证、测试和可重放的研究模型交付。暂不补充 COMSOL，不启动范围外候选搜索，不等待新增实测。范围外的 frame 横向声学机制只形成有证据边界的后续检查规格。

执行链固定为：执行模型只读复述 → 高级规划者消除歧义并冻结版本 → D01–D08 顺序实施 → 每阶段另一代理独立审查 → 自动验收。提示词本身不能保证正确执行，真实结果、机器检查与独立审查共同构成门禁。

## 1. 已核实事实与本轮边界

2026-11-05 规划时实际读取了现有 B 标签、协议和训练实现，并计算了下面的标签身份、结构计数、分组规则和四个文件摘要；**没有重新训练、没有重新聚合器件标签、没有核验整个旧交付树或归档包**。

- 主标签有 576 个结构、288 个设计组、26,480 个唯一器件身份。每设计组恰有两个结构，属于同一结构分支；器件身份没有跨设计组重叠。
- 标签表有 9 个参数，每参数每结构一行。四个建模目标固定为 `k2eff_pct, qp, qs, bodeq_fitted`。结构标签是逐参数所有合格器件的中位数，训练时每结构等权。
- 六个结构分支的设计组数依次为 6、42、24、168、6、42。旧 normal_holdout 测试未覆盖两个各有 6 组的分支；本轮不能复用它来宣称六分支均有测试。
- 同一 EG/FL/分支下有六组配对面积：`(700,5500)、(900,5000)、(1200,4500)、(1500,4000)、(2000,3500)、(2500,3000)`，单位 µm²。配对结构不能拆到不同角色。
- 原始历史标签过去已被研究流程使用。本轮所有留出仍为**回顾性验证**，不是新盲测；三个评估 scope 也不是三份独立实验。
- 原 COMSOL 准备工作 D04 的 FAIL 不阻塞本轮统计训练。不能修改原 FAIL，不能用本轮通过替代仿真输入资格。
- 历史去嵌配置、FL 与实际 frame 尺寸的对应关系尚未核实。这限制物理解释，不授权重写标签，也不作为继续统计训练的阻塞项。

## 2. 固定路径、权限与工具

以下变量在全文只有一个含义。所有回执中展开为绝对路径。

```text
MAIN=/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master
ML=/Users/jingbozuo/Projects/aln-data-backend-ml
BE=/Users/jingbozuo/Projects/aln-data-backend-ml/backend
B=/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_device_median_v1/attempt_001
LEGACY=/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_domain_candidates_v1/attempt_001
POSTSEAL=/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/postseal_3d_v1/attempt_002
RUN=/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/in_domain_training_v1/attempt_001
PLAN=/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/docs/superpowers/plans/2026-11-05-surrogate-in-domain-executable-spec.md
PROMPTS=/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/docs/superpowers/plans/2026-11-05-surrogate-in-domain-agent-prompts.md
LABELS=B/data/structure_targets_primary.csv
SENSITIVITY=B/data/structure_targets_sensitivity.csv
SOURCE=BE/scripts/run_device_median_models_v1.py
SCRIPT=RUN/code/in_domain.py
```

`RUN` 是本计划新命名空间，不是任何旧 attempt 的替代，不登记 supersedes。若该路径已存在且不是本计划已核验的接续现场，停止，不能自行选 attempt_002。

| 允许工具/操作 | 参数或限制 |
|---|---|
| 文件读取、`rg`、`git status --porcelain=v1` | 只读。Git 使用 `GIT_OPTIONAL_LOCKS=0`；不 checkout、reset、clean、commit、push |
| Shell | `cwd=BE`；使用绝对路径；不读取 `.env`、数据库、密钥 |
| Python | `/Users/jingbozuo/.local/bin/uv run --no-sync python -B`；环境 `PYTHONDONTWRITEBYTECODE=1`；不安装包、不更新 lock |
| 新文件写入 | 仅本阶段在 RUN 中拥有的目录；禁止覆盖已经封存的产物/回执 |
| 哈希 | Python `hashlib.sha256`，以二进制分块读取；不是文件时间戳、Git hash 或文件名 |
| 后台长任务 | `/opt/homebrew/bin/tmux`；唯一会话名、stdout/stderr、exit_code；禁止轮询 |
| 子代理 | 使用运行时真实可用的 spawn/Agent 工具；记录工具返回的身份。无子代理能力则 BLOCKED，不模拟 A4 |
| 网页读取 | A3 在 D01 双PASS后的 preparation/frame 准备及正式 D07 可读取第8节四个来源；其他角色不扩展检索，不下载大型包 |

规划时本机版本：numpy 2.4.4、pandas 3.0.2、scipy 1.17.1、scikit-learn 1.9.0、joblib 1.5.3、threadpoolctl 3.6.0、pytest 9.0.3。D01 若版本不同，停止交高级规划者判断；执行者不能升级或自行接受差异。Python 版本在 D01 实录，必须 ≥3.12。

### 2.1 必须匹配的源文件

| 路径 | SHA256 |
|---|---|
| B/protocol.json | `f2e1e8a241018e779bc0e6c990f93b7cf8b890c93355a5d9468549a2593288ee` |
| LABELS | `230b18dcf6db91707ec51f66ef31ab7fe3284c4085bb5499ef7680f4b0dfc134` |
| SENSITIVITY | `50ef2e1b3dc3fed03325a3ddcf713ee6b5845a89555eb3b66cf11bba94d06a19` |
| SOURCE | `275cf8400e970843fdf4366551ac4df1221615aaeb227276eabc239521a2dbe0` |

禁止重哈希 `#2.zip` 或重新扫描 700 个归档成员；这与本轮训练输入无关。

## 3. 数据、划分与统计契约

### 3.1 术语和读入

- `structure_key`：结构身份。`p3_design_group_id`：必须整体划分的设计组。`p3_branch_id`：独立拟合的类别分支。
- 六分支只允许：`pf0_eg0_fl0, pf0_eg0_fl1, pf0_eg1_fl0, pf0_eg1_fl1, pf1_eg0_fl0, pf1_eg0_fl1`。不制造数据中不存在的分支。
- `eg_um/fl_um/area_um2` 是登记坐标，单位分别 µm/µm/µm²；`has_pf` 是布尔类别。它们不是已确认的 frame 物理尺寸或制造规则。
- `role` 仅允许 `train/validation/test/excluded`。`scope` 仅允许 `group_holdout/eg_gap/fl_gap`。
- CSV 统一 UTF-8、无索引，按明确键稳定排序；浮点读取用 `float_precision="round_trip"`，写出用 `float_format="%.17g"`。JSON 用 UTF-8、排序键、禁止 NaN/Infinity，缺失用 null。
- 不改变原中位数。训练资格：`mode=deembedded`、median 有限且 >0、`n_valid>=5`、`low_support=false`；k2eff_pct 另需 `<100`。本轮主表预期四目标每个 576 行均合格。若不符，停止，不擅自删行。
- 将 `has_pf` 的布尔值或字符串 True/False 转为 0/1；其他值报错。`low_support` 同样严格解析，不对非空字符串直接 `bool()`。
- 读取 `source_device_keys` JSON 数组，只检查结构/设计组身份及跨角色重叠，不重算器件参数。四目标结构身份相同；同一结构不同目标必须使用同一 role。
- 模型按结构等权，不按 `n_valid` 加权；不把重复器件拆成训练行；不根据文件名 Fail、Q 高低或拟合误差筛选标签。

### 3.2 主划分：group_holdout

只使用几何和身份，不使用目标值选择划分。固定 seed 字符串 `20261105`。

对每个分支执行以下确定算法：

1. 取该分支 EG、FL、area 的最小/最大值。将三个轴都处于各自端点的结构称为角点；零范围轴只有一个端点。
2. 包含任一角点的设计组全部置于 train，称为 anchor。对本数据六分支 anchor 组数必须分别为 `1,2,2,4,1,2`。
3. 将其余设计组按 `(sha256(UTF8("20261105|"+group_id)).hexdigest(), group_id)` 升序排序。
4. `n = max(1, floor(0.15 * 本分支总组数 + 0.5))`。剩余排序的前 n 组为 test；接着 n 组为 validation；其余和 anchor 为 train。
5. 整组两结构、全部目标沿用同一角色。不得换 seed、交换组或根据结果重新抽样。
6. 所有分支的几何角点必须仍在 train。因此在 `(EG,FL,log(area))` 坐标下，验证和测试点均在训练点凸包内。这个几何条件不代表预测一定准确。

| 分支 | 总组 | anchor | train 组 | validation 组 | test 组 |
|---|---:|---:|---:|---:|---:|
| pf0_eg0_fl0 | 6 | 1 | 4 | 1 | 1 |
| pf0_eg0_fl1 | 42 | 2 | 30 | 6 | 6 |
| pf0_eg1_fl0 | 24 | 2 | 16 | 4 | 4 |
| pf0_eg1_fl1 | 168 | 4 | 118 | 25 | 25 |
| pf1_eg0_fl0 | 6 | 1 | 4 | 1 | 1 |
| pf1_eg0_fl1 | 42 | 2 | 30 | 6 | 6 |
| 合计 | 288 | 12 | 202 | 43 | 43 |

结构数固定为 **404/86/86**。主训练比例约 70.1%，验证和测试各约 14.9%。不随机拆器件。

### 3.3 两个范围内水平留出

这两项用于检验只记住已出现 EG/FL 水平的问题；仍属于训练边界内部的留出，不能称范围外验证。

| scope | 纳入分支 | train | validation | test | 结构数 train/val/test |
|---|---|---|---|---|---|
| eg_gap | pf0_eg1_fl0、pf0_eg1_fl1 | EG=0.75 或 2.0 | EG=1.0 | EG=1.5 | 192/96/96 |
| fl_gap | pf0_eg0_fl1、pf0_eg1_fl1、pf1_eg0_fl1 | FL=0.5、0.75、1.25、1.75、2.0 | FL=1.0 | FL=1.5 | 360/72/72 |

其余分支 role=excluded 并保留在划分清单，不拟合、不计分。每 scope 自己训练、自己选模、自己测试，不借用另一 scope 的模型、评分或选择。各 scope 可重用历史结构，但角色隔离必须在 scope 内成立。全部 scope 的候选配置一次冻结，不能看某 scope 的测试结果后调整其他 scope。

### 3.4 精确训练候选与选择

每个 `(scope,target)` 仅运行下面六候选，一共 12 个单元、72 次顶层 fit 调用。内部每分支拟合属于同一 fit，不混淆计数。不进行随机搜索、自动调参、交叉验证扩容或神经网络重训。

| ID（也是并列时优先级，从上到下） | 模型定义 |
|---|---|
| C00 | train 中本目标的全体结构原单位中位数常量 |
| C01 | train 中本目标按 p3_branch_id 分支的原单位中位数常量 |
| C02 | 复用 `ExtraTreesScalar`；min_samples_leaf=1 |
| C03 | 复用 `ExtraTreesScalar`；min_samples_leaf=2 |
| C04 | 复用 `TrendGPLogAreaScalar`；length_scale=0.5 |
| C05 | 复用 `TrendGPLogAreaScalar`；length_scale=1.0 |

固定其余参数：

```json
{
  "extra_trees": {
    "feature_order": ["eg_um", "fl_um", "area_um2"],
    "n_estimators": 192, "max_features": 1.0,
    "random_state": 20261105, "n_jobs": 1
  },
  "trend_gp": {
    "feature_order_after_transform": ["eg_um", "fl_um", "log_area_um2"],
    "trend": {"alpha": 1.0},
    "gp": {
      "length_scale_bounds": "fixed", "nu": 2.5,
      "noise_level": 0.05, "noise_level_bounds": "fixed",
      "alpha": 1e-10, "optimizer": null,
      "normalize_y": true, "random_state": 20261105
    }
  }
}
```

`length_scale` 和 `min_samples_leaf` 由候选表填入。仅复用 SOURCE 中两个类，不调用旧 CLI。把 BE 加到 `sys.path` 后用 `from scripts.run_device_median_models_v1 import ExtraTreesScalar, TrendGPLogAreaScalar`；不修改 SOURCE，不复制改名成第二种独立模型。C00/C01 存储数值或分支字典，不创建不可反序列化的临时类。

树/GP 的缩放、趋势、log 变换全部仅用该 scope 的 train；验证/测试不参与拟合。保留既有按分支独立拟合、log-target 回变换实现，不增加“frame 约束”损失。

对每单元，用 validation 的原单位 MAE 最小者作为 selected。并列定义为与最小 MAE 的差 `<=1e-12`，按 C00→C05 优先级取第一个。即便常量胜出也接受，不强制选择复杂模型。12 个选择必须全部写出并封存，之后才读取测试标签计算预测误差。**不把 train+validation 合并再拟合，不用全部数据重新拟合交付模型。** 交付模型就是此次被评分的 train-only 实例。

C02/C03 是一种方法的配置，C04/C05 也是一种方法的配置，不能按四个独立物理模型宣传。B 内层胜者与 P6C 同方法的旧事实不改变。

### 3.5 指标、区间和负结果

每 scope/target/candidate/role 的逐点预测表保留真值和预测。`error=prediction-truth`；MAE=`mean(abs(error))`，RMSE=`sqrt(mean(error**2))`，bias=`mean(error)`，P90=`numpy.quantile(abs(error),0.9,method="linear")`。不以 R² 作为通过门槛。

- k2eff_pct 的误差单位是**百分点**，不是相对百分比；三个 Q 目标无量纲。
- 单元总体及每分支分别计分；按设计组汇总的 MAE 取该组两个结构绝对误差的平均，再对组等权平均。本数据组大小一致，必须与结构 MAE 一致（绝对差≤1e-10）。
- 测试仅计算 selected、C00、C01 三个条目的预测；若 selected 恰为常量仍按三个角色登记，但保存 `candidate_id`，不计作新增独立模型。
- 对 selected 与 C01 的测试 MAE 差，计算簇感知 bootstrap：按分支分层，每层从其设计组中有放回抽取原组数，抽中的组携带全部两结构；2,000 次；scope 顺序 group_holdout/eg_gap/fl_gap、target 顺序本节四目标，seed=`20261105+100*scope_index+target_index`，index 从0起。用 `numpy.random.default_rng(seed)`；分支/组字典序排序；逐重复逐分支抽取。
- 输出 selected MAE、C01 MAE、`delta=C01_MAE-selected_MAE` 的 2.5%/97.5%线性分位数。它是**历史测试 MAE/差值的重采样区间**，不是逐点预测区间，不证明范围外覆盖。
- 单分支测试组数<10时标 `LOW_TEST_GROUP_COUNT`，只报告描述统计，不单独做分支置信区间或宣称稳定；总体分层区间须注明单组小分支无法估计组间变化。
- `relative_gain=(C01_MAE-selected_MAE)/C01_MAE`；C01_MAE=0 时设 null、状态 `BASELINE_ZERO_NO_GAIN_ESTABLISHED`，不得除零。
- 本轮预登记的**研究门槛**：validation selected_MAE < C01_MAE；test relative_gain≥0.05；test delta 的95%区间下界>0；selected 所有预测有限，Q>0，0<k2eff_pct<100。全部满足为该单元 `SUPPORTED_RETROSPECTIVELY`；否则为 `NOT_ESTABLISHED`。5%是本轮对基线的研究比较门槛，不是器件精度规格。
- 主结论仅由 group_holdout 的 k2eff_pct、qp 两单元决定：两者均通过为 `IN_DOMAIN_RESEARCH_SUPPORTED`，其余为 `NEGATIVE_RESULT`。Qs/BodeQ 和水平留出分别报告，不偷换主结论；EG/FL 泛化只在各自单元通过时表述为对应历史水平留出得到支持。
- 科学负结果是合法正常交付，继续 D07/D08，实施回执仍可 PASS。预测违反物理数值范围是科学失败证据，保留原预测，不截断修饰；不能变成“通过约束优化后成功”。
- 输出缺行、非有限运算导致文件无法生成、数据泄漏、模型未拟合、命令失败是执行错误，不能记为科学负结果。禁止为了成功改阈值或补跑候选。

Qp 敏感性只在主选择冻结后做，每scope最多一次fit，总上限3次；不重新选模、不替代主结果。集合和计分固定如下：

1. `T_p/U_p` 是该scope主标签的train/test结构ID；`E_s` 是SENSITIVITY中满足第3.1节qp资格的ID；敏感性训练集 `T_s=T_p∩E_s`，敏感性测试集 `U_s=U_p∩E_s`。使用该scope已经选中的candidate配置，禁止移动role、借用validation补样本。
2. 资格按目标/结构判定。组内一个结构不合格时仅排除该结构，另一结构仍保留原role；记录被排除ID/原因。设计组仍不能跨role。若T_s或U_s为空，或T_s未覆盖原T_p所有分支，则该scope敏感性记NOT_EVALUABLE、不fit；不拿primary模型冒充。若U_s少某个分支，保留其他分支评价，明确缺失分支和测试数，不补预测。
3. 定义 `C=U_p∩U_s`（即U_s）。native_primary是原primary预测在U_p上对primary真值的MAE；native_sensitive是敏感性模型在U_s上对sensitivity真值的MAE。这两个native分数标签/样本可能不同，不能相减声称模型改进。
4. common比较固定使用C上的**primary真值**，分别计算primary预测和敏感性预测的MAE；另列C上的primary/sensitivity真值差的MAE和bias。敏感性模型在C上对sensitivity真值的MAE也单列，但不能与common主比较混用。
5. 敏感性只做描述性MAE/RMSE/bias，不另做bootstrap或科学门槛；common的结构可不满每组两行，不套用主研究的两结构组均值等式。保存native/common行数、组数、分支数、训练交集及差集。T_s不等于T_p时标 `TRAIN_POPULATION_CHANGED`；该对照不能被解释为纯标签效应。
6. 不得按敏感性表现重选主模型、改变标签或回到D04。NOT_EVALUABLE是已说明的附加诊断缺项，不阻塞主研究交付。

## 4. 产物、状态和审核协议

### 4.1 文件责任

| 角色 | 责任 | 可写目录 |
|---|---|---|
| A0 协调/集成 | D01、D05、D08，派发、门禁、命令收集 | 对应 stages/Dnn、对应 receipts/Dnn.json；D01 初始化目录、D08 根 README/final_report/final_manifest |
| A1 数据与训练 | D02、D04、D06 | 对应 stages/Dnn、对应 receipts/Dnn.json |
| A2 实现 | D03 | code、tests、stages/D03、receipts/D03.json |
| A3 物理分支 | D01 审核后可独立整理指定来源；D07 才正式交付 | preparation/frame、stages/D07、receipts/D07.json |
| A4 独立审查 | 审核每阶段，不参与实现或选参 | review/Dnn |

以上是任务分支，不要求创建 Git 分支或合并工作树。所有新代码位于 RUN，旧 ML/main 文件只读。A3 的 preparation 是明确允许的并行预备工作，不能写 D07 回执或释放阶段。其余 Dnn 不得提前启动。D04/D06的job、launch和日志由A1写；A0仅派发和协调，不写A1阶段文件。最多同时三个实施角色与一个审查角色；槽位不足时缩为 A0、当前实施者、A3/A4 三角色，不通过递归派生绕过。

### 4.2 每阶段两份独立回执

实施回执固定为 `RUN/receipts/Dnn.json`，不覆盖。最低 schema：

```json
{
  "plan_id": "SURROGATE_IN_DOMAIN_V1", "plan_sha256": "实际摘要",
  "stage_id": "D01", "status": "PASS",
  "outcome": "COMPLETED", "author_agent_id": "实际ID",
  "contributor_agent_ids": [], "started_at": "ISO8601", "finished_at": "ISO8601",
  "inputs": [{"path": "绝对路径", "bytes": 0, "sha256": "实际摘要"}],
  "outputs": [{"path": "绝对路径", "bytes": 0, "sha256": "实际摘要"}],
  "commands": [{"argv": [], "cwd": "绝对路径", "exit_code": 0, "log_path": null}],
  "checks": [{"id": "具体检查名", "status": "PASS", "evidence": "真实路径、字段、数值"}],
  "issues": [], "trusted": false, "legacy_d09": "NOT_RUN", "legacy_d10": "NOT_RUN"
}
```

status 仅 PASS/FAIL；outcome 为 COMPLETED/NEGATIVE_RESULT/EXECUTION_ERROR/NEEDS_REPLAN。不得照抄示例中的 PASS、0、空数组代替结果。issues 项必须含 `id,evidence,impact,required_decision`；没有问题才为空。

A4 写 `RUN/review/Dnn/receipt.json`：含 `status`、实际 `reviewer_agent_id`、implementation_receipt 的路径/SHA256、`checks/findings`、`read_files/recomputed_items/rehashed_files`、`released_next_stage`、`trusted=false`。reviewer 不得出现在本阶段 author/contributors 中；同一真实独立 A4 可以连续审各阶段，但不能自审其参与实现的阶段。

只有实施 PASS、审核 PASS、下一阶段号正确、依赖哈希仍匹配才能进入下一阶段。D08 审核的 released_next_stage=null。A4 失败即停，实施者不得边修边覆盖旧回执。首次运行中的未封存实现代码只允许第 9 节规定的局部修复；封存后问题交高级规划者改版本、指定新的修复目录和重跑起点。

### 4.3 长任务契约

训练超过20秒预期即后台启动。每个 stage 只启动一次，模板由 A0 展开绝对路径并保存实际 argv；会话名 `indomain_20261105_D04_` 加当前进程 PID，避免撞名。

```bash
# 在 BE 执行；RUN 必须先赋值为第2节的完整绝对路径。
tmux new-session -d -s "indomain_20261105_D04_$$" \
  "bash '$RUN/stages/D04/job.sh'"
```

`job.sh` 只运行该阶段登记命令，不安装依赖、不清理文件。A1使用以下固定模板：D04时STAGE=D04、COMMAND=train；D06时STAGE=D06、COMMAND=evaluate，不存在第三种映射。

```bash
#!/usr/bin/env bash
set +e
RUN='/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/in_domain_training_v1/attempt_001'
STAGE='D04'
COMMAND='train'
export PYTHONDONTWRITEBYTECODE=1 GIT_OPTIONAL_LOCKS=0
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
run_job() {
  cd '/Users/jingbozuo/Projects/aln-data-backend-ml/backend' || return "$?"
  /Users/jingbozuo/.local/bin/uv run --no-sync python -B "$RUN/code/in_domain.py" "$COMMAND" --run "$RUN"
}
run_job > "$RUN/stages/$STAGE/stdout.txt" 2> "$RUN/stages/$STAGE/stderr.txt"
job_rc=$?
printf '%s\n' "$job_rc" > "$RUN/stages/$STAGE/exit_code.tmp"
mv "$RUN/stages/$STAGE/exit_code.tmp" "$RUN/stages/$STAGE/exit_code.txt"
exit "$job_rc"
```

超时上限D04=1800秒、D06=900秒，由SCRIPT对其一个worker子进程使用`subprocess.run(..., timeout=...)`执行；超时返回124并保留日志，不重启。不得在已有exit_code或launch的现场重新执行job.sh。

启动后立即做A3物理资料准备、A0只读整理报告所需字段或独立审查等不依赖训练结果的工作；A0只能在内存准备，不能提前写D08文件。禁止轮询、循环sleep、重复打印“仍在运行”。需要结果时允许**一次**检查exit_code是否存在；存在则读退出码和日志一次，不存在则只报告一次PENDING和接续命令后结束当前回合。下一次由真实完成事件或外部续接消息触发再检查一次。等待句柄超时不是训练失败或成功；不能因为缺少事件而永远拒绝读取现有退出码。禁止主动定时重复发起续接。

后台未完成时不写实施 PASS/FAIL 回执，不释放下一阶段；launch.json 记录 job ID、日志路径、启动时间和命令 hash。已有 launch 的接续只收集结果，绝不重新 launch。无 exit_code 且进程已消失、退出码非零、超预算均停止并报告。

## 5. 分阶段执行

所有步骤只可消费本计划和明确列出的输入。代码命令在 D03 之前是**待实现接口**，不能当作现有功能直接运行。每一步失败时仅执行其失败分支；其余情况标 NEEDS_REPLAN。

### D01：隔离现场与输入登记

- 输入：冻结的 PLAN、执行模型复述文件及高级规划者的 `readback_review.json`（status=PASS，绑定 PLAN 的 SHA256）；第2节四个源文件。
- 动作：
  1. **步骤编号：S01。目的：建立唯一可写现场。依赖步骤：复述审查 PASS。**
     - 输入：上述文件、RUN 路径、工具版本。
     - 前置条件：PLAN hash 与 readback_review 相同；RUN 不存在；四个源 hash 一致；版本符合第2节。
     - 具体操作：只读检查前置条件；用 `Path(RUN).mkdir(parents=True,exist_ok=False)` 创建 RUN；创建 `code,tests,preparation/frame,receipts,review,stages` 及 stages/D01…D08；复制复述及其审查到 stages/D01，不改正文。
     - 工具或命令模板：BE 下 `uv run --no-sync python -B` 的一次标准库 preflight；`importlib.metadata.version` 读版本，hashlib 读摘要，Path.mkdir 建目录。创建前失败只输出事实，不创建半有效 RUN。
     - 预期产出及保存位置：`stages/D01/preflight.json`、`source_manifest.json`、`readback.md`、`readback_review.json`、`git_status_before.txt`。
     - 完成判定：四个预登记 hash 匹配；版本/路径有效；现场唯一；上述文件完整。
     - 验证方法：A4 独立复哈希四文件和 PLAN，读取 mkdir/版本结果；核对未登记替代旧 attempt。
     - 失败处理：不存在的输入、版本差异、hash 不同、RUN 冲突均立即停止；不下载、不切分支、不另建 attempt。
     - 禁止事项：启动训练、读取历史模型测试指标来改门槛、修改现有代码和封存。
- 完成标准：S01通过，数据版本已绑定，写权限边界成立。
- 回执：A0 写 receipts/D01.json，含实际产物和 SHA256、遗留问题。
- 独立审查：A4 写 review/D01/receipt.json；PASS 仅释放 D02，并允许 A3 开始 preparation/frame。

### D02：冻结数据角色与训练协议

- 输入：D01 实施/独立 PASS 回执及 source_manifest；LABELS、SENSITIVITY、B/protocol.json；第3节完整契约。
- 动作：
  1. **步骤编号：S02。目的：生成唯一可执行的数据角色清单。依赖步骤：S01、D01审查。**
     - 输入：数据身份、几何列、资格字段和 source_device_keys；不使用目标高低选择角色。
     - 前置条件：四源摘要未变；只允许已列六分支及六种面积配对。
     - 具体操作：按第3节精确算法生成三个 scope 的角色；检查同结构多目标 metadata 一致；展开器件 ID 做 scope 内角色集合交集；按 scope/structure_key 排序；生成包含所有固定参数和门槛的 protocol.json，记录几何角点/配对/资格检查。
     - 工具或命令模板：A1 在 `stages/D02/build_protocol.py` 实现第3节确定算法，执行一次 `uv run --no-sync python -B "$RUN/stages/D02/build_protocol.py" --run "$RUN"`；该脚本不导入训练类、不调用 fit、不展示测试标签摘要。
     - 预期产出及保存位置：`stages/D02/protocol.json`、`split_manifest.csv`、`split_summary.csv`、`data_checks.json`、`build_protocol.py`、命令日志。
     - split_manifest 列：`scope,structure_key,p3_structure_id,p3_design_group_id,p3_branch_id,eg_um,fl_um,area_um2,has_pf,role,anchor`；三个 scope 各576行，合计1728行，excluded 保留。
     - 完成判定：主角色404/86/86；eg_gap192/96/96及excluded192；fl_gap360/72/72及excluded72；scope内结构/组/器件无跨角色重叠；每训练分支完整角点；四目标每结构合格。
     - 验证方法：A4 从源元数据独立重建身份排序、anchor与角色，不调用 build_protocol；逐行对比；报告实际复算项目，不只检查计数。
     - 失败处理：任何计数/字段/组关系不同立即停；不得修补标签、抽第二个seed或放宽凸包条件。
     - 禁止事项：把 n_valid 当权重；按性能选 test；读取旧测试结果作为选模依据。
- 完成标准：角色、模型配置、选模/验收规则均机器可读且与本文一致。
- 回执：A1 写 receipts/D02.json，登记脚本/协议/清单 SHA256、遗留问题。
- 独立审查：A4 PASS 后释放 D03；协议和清单从此不可修改。

### D03：实现受限训练、评分与重放入口

- 输入：D02 双PASS及全部冻结产物；SOURCE；第6节接口/算法/自动检查。
- 动作：
  1. **步骤编号：S03。目的：实现规定计算，不运行真实训练。依赖步骤：S02、D02审查。**
     - 输入：冻结 protocol、split schema、现有两个模型类。
     - 前置条件：仅 code/tests/stages/D03 可写；输入hash一致。
     - 具体操作：建立 SCRIPT 与 tests/test_in_domain.py；按第6节实现 train/select/evaluate/physics/accept/predict 子命令；不增加模型/超参；joblib 仅存既有可导入类与普通字典；固定数据读写、日志和退出码。
     - 工具或命令模板：使用文件编辑工具写新文件；禁止执行旧 run_device_median_models_v1.py CLI。
     - 预期产出及保存位置：`code/in_domain.py`、`tests/test_in_domain.py`、`stages/D03/implementation_contract.md`（列接口与对应函数）。
     - 完成判定：第6节每项有明确函数/输出对应；无测试标签用于拟合/选择、无新的依赖。
     - 验证方法：S04运行唯一针对性单测；A4另做代码数据流审查。
     - 失败处理：接口无法按现有依赖实现则停止说明，不替换第三方库或改老代码。
     - 禁止事项：实数据拟合、后台训练、读取未登记数据、修改D02。
  2. **步骤编号：S04。目的：验证代码合同。依赖步骤：S03。**
     - 输入：新代码与第6.3节固定测试。
     - 前置条件：测试使用合成小表/常量桩，不运行真实训练研究。
     - 具体操作：运行一次针对性 pytest；保留完整stdout/stderr与退出码。
     - 工具或命令模板：`uv run --no-sync python -B -m pytest -q -p no:cacheprovider "$RUN/tests/test_in_domain.py"`。
     - 预期产出及保存位置：`stages/D03/pytest.txt`、`pytest_exit_code.txt`。
     - 完成判定：全部指定测试通过，退出码0。
     - 验证方法：A4读测试内容与日志；不把测试数量当覆盖证明，检查第6.3节每项断言。
     - 失败处理：只允许一次针对报错的本阶段局部修复和一次重跑；第二次失败停止。保留两次日志；不重写验收条件让测试通过。
     - 禁止事项：全仓库pytest/lint/build、修复无关故障。
- 完成标准：接口已实现且针对性检查通过；仍无真实fit。
- 回执：A2写 receipts/D03.json，绑定所有代码/测试hash。
- 独立审查：A4复核数据流、限次/超时、验证测试，PASS才释放D04。之后代码不可热修。

### D04：有限训练和验证预测

- 输入：D03双PASS和封存代码；D02协议/划分；主标签。
- 动作：
  1. **步骤编号：S05。目的：完成预登记的72次候选fit与验证预测。依赖步骤：S04、D03审查。**
     - 输入：三个scope各自train/validation；四目标；六候选。
     - 前置条件：D04没有launch记录、模型、exit_code或回执；代码/输入hash不变。接续已有launch时只收结果。
     - 具体操作：按 scope、target、candidate 固定顺序运行；每次fit只传train；仅对train和validation输出预测；记录实际训练身份/缩放参数/实例；保存所有候选，不读test误差；通过第4.3节后台机制执行。
     - 工具或命令模板：`uv run --no-sync python -B "$SCRIPT" train --run "$RUN"`，包在D04/job.sh；BLAS/OMP线程设1，threadpoolctl limits=1。
     - 预期产出及保存位置：`stages/D04/models/{scope}/{target}/{candidate}.joblib`、`train_predictions.csv`、`validation_predictions.csv`、`validation_metrics.csv`、`fit_ledger.jsonl`、`run_summary.json`、job/launch/日志/exit_code。
     - 完成判定：72顶层fit均成功；逐点预测行数train=22944、validation=6096；模型文件72；测试预测0行；退出码0；没有参数搜索。
     - 验证方法：A4将ledger中的train结构/组集合与split逐单元精确比对，核对模型配置、缩放边界和预测身份；复算全部validation MAE。审查不重新fit。
     - 失败处理：超时124、非零退出、缺模型或行数异常即停；不重启、不减少候选、不跳单元。
     - 禁止事项：train+validation refit、根据验证结果追加参数、评价测试。
- 完成标准：有限任务完整，验证误差可复算，所有训练身份可追溯。
- 回执：A1写receipts/D04.json，含实际fit数、时间、全部产物hash、遗留问题。
- 独立审查：A4 PASS后释放D05。

### D05：一次选模并冻结测试输入

- 输入：D04双PASS、validation_predictions/metrics、模型；D02选择规则。
- 动作：
  1. **步骤编号：S06。目的：冻结12个选择，不拟合。依赖步骤：S05、D04审查。**
     - 输入：validation误差及六候选固定顺序。
     - 前置条件：没有test_predictions或测试指标；D04模型hash匹配。
     - 具体操作：按第3.4节最小MAE/1e-12并列规则选定每单元；写 selected.json，逐项引用原模型路径/hash；写测试允许读取的test ID/hash与基线ID；只引用模型，不复制覆盖、不再次fit。
     - 工具或命令模板：`uv run --no-sync python -B "$SCRIPT" select --run "$RUN"`。
     - 预期产出及保存位置：`stages/D05/selected.json`、`selection_table.csv`、`test_freeze.json`。
     - 完成判定：12项选择完整、配置/实例唯一，selection只来源validation，fit新增数0。
     - 验证方法：A4直接从validation_predictions复算MAE和排序，精确核对选中ID及模型SHA256。
     - 失败处理：证据不一致停止，不用测试替代验证、不人工指定更复杂模型。
     - 禁止事项：任何重新拟合、参数修改、剔除差分支。
- 完成标准：12个选择和测试边界全部冻结。
- 回执：A0写receipts/D05.json，记录选模产物hash、遗留问题。
- 独立审查：A4 PASS后释放D06；此前不能计算test误差。

### D06：一次测试、簇感知评价与Qp敏感性

- 输入：D05双PASS及selected/test_freeze；D04被选模型和C00/C01；LABELS和SENSITIVITY。
- 动作：
  1. **步骤编号：S07。目的：生成冻结模型的测试预测。依赖步骤：S06、D05审查。**
     - 输入：test角色几何/标签、选中模型、两个常量基线。
     - 前置条件：D06未launch；所有选择和模型hash一致。
     - 具体操作：对12单元生成selected/C00/C01预测；按第3.5节明确的T_s/U_s/C集合和NOT_EVALUABLE分支完成3次上限Qp敏感性fit，单独保存；不重新选择；后台运行一次evaluate命令（其内部先预测、后S08计分）。
     - 工具或命令模板：`uv run --no-sync python -B "$SCRIPT" evaluate --run "$RUN"`；第4.3节，timeout=900秒。
     - 预期产出及保存位置：`stages/D06/test_predictions.csv`、`sensitivity_predictions.csv`、`sensitivity_fit_ledger.jsonl`、敏感性模型、job/launch/日志/exit_code。
     - 完成判定：primary测试预测3048行；角色selected/C00/C01各1016行；所有ID精确等于各scope测试集×四目标；primary新增fit为0；sensitivity fit≤3。
     - 验证方法：A4按模型hash重放预测，不fit；核对同scope无训练身份混入test。重放浮点误差要求 `abs_diff<=1e-10+1e-10*abs(expected)`。
     - 失败处理：非零退出、身份或行数不符即停；物理数值不合法但可有限记录时保留预测并使科学门槛不通过；禁止剪裁。
     - 禁止事项：看测试追加训练、把敏感性结果替换主结果、宣布前瞻通过。
  2. **步骤编号：S08。目的：按冻结规则形成研究结论。依赖步骤：S07预测文件已完整写出。**
     - 输入：逐点预测、validation指标、固定bootstrap规则。
     - 前置条件：预测文件身份/行数检查通过；没有自由裁量门槛。
     - 具体操作：evaluate内部按第3.5节计算总体/分支指标、簇bootstrap和12个门槛状态；独立列Qp主/敏感性 common/native 对照；生成科学结论，不改模型。
     - 工具或命令模板：S07的同一次evaluate内固定调用score函数，不另启动训练命令。
     - 预期产出及保存位置：`stages/D06/test_metrics.csv`、`branch_metrics.csv`、`bootstrap_intervals.csv`、`sensitivity_comparison.csv`、`scientific_result.json`。
     - 完成判定：12单元均有结果；所有失败门槛逐项写出；稀疏分支标记齐全；负结果也有完整可复算表。
     - 验证方法：A4用独立表达式复算全部MAE/RMSE/bias/P90、组均值、固定seed的bootstrap和门槛，不调用被审查score函数；登记实际复算范围。
     - 失败处理：公式/区间复算不一致属于执行错误，停止交高级规划者；研究门槛不通过属于正常负结果，继续D07。
     - 禁止事项：把MAE区间叫预测区间，把三个scope叫独立实验，把5%门槛叫制造资格。
- 完成标准：测试只运行一次、选择未改、科学状态和计算完整性分开。
- 回执：A1写receipts/D06.json；负结果使用status=PASS、outcome=NEGATIVE_RESULT。
- 独立审查：A4对完整正/负结果均可PASS，PASS释放D07，不准为获得好结果回D04。

### D07：frame物理边界与范围外拒绝策略

- 输入：D06双PASS、科学结果；第8节固定来源；POSTSEAL/stages/D04/questions.md；A3 preparation/frame材料。
- 动作：
  1. **步骤编号：S09。目的：交付有明确适用范围的物理检查规则。依赖步骤：S08、D06审查。**
     - 输入：已有几何身份、标量模型、实际缺项、固定物理证据。
     - 前置条件：不启动COMSOL、归档处理、范围外预测或候选优化。
     - 具体操作：按第8节逐条生成physics_policy.json和frame_physics.md；将frame数值约束能力标NOT_EVALUABLE_WITH_CURRENT_INPUTS；将predict入口范围外/未知分支拒绝规则列入接口文档；引用数值检查失败，仅作为有限/正值检查，不能宣称复阻抗守恒已验证。
     - 工具或命令模板：`uv run --no-sync python -B "$SCRIPT" physics --run "$RUN"` 仅写确定的机器策略；A3据第8节写中文说明，不调用fit或额外预测。
     - 预期产出及保存位置：`stages/D07/physics_policy.json`、`frame_physics.md`、`inference_contract.md`。
     - 完成判定：明确frame可能通过反射/模态转换/匹配和外区倏逝限制能流；明确Type-I/II不可照搬；FL物理定义缺失；无FL→Q单调硬约束；范围外预测默认拒绝；trusted=false。
     - 验证方法：A4逐条对照已列来源与本地缺项；区分作者实测/建模结果、执行者推论及本项目未验证项；机器检查策略枚举和拒绝状态。
     - 失败处理：来源访问失败可引用本计划给出的书目信息与摘要范围，注明“本次未取得全文”；不得编页码/定量阈值。发现需要新增物理事实时登记未评估，不阻塞本轮统计交付。
     - 禁止事项：假定FL=frame宽、从面积假定方形周长、假造波速/层厚、以Q标量证明能流约束。
- 完成标准：物理边界完整，额外物理输入缺失不形成新的训练等待循环。
- 回执：A3写receipts/D07.json，列来源访问范围、产物hash和物理未评估事项。
- 独立审查：A4 PASS后释放D08。

### D08：自动验收、研究交付与后置独立审核

- 输入：D01–D07双PASS回执、全部研究产物、第10节验收表。
- 动作：
  1. **步骤编号：S10。目的：生成可审查的最终交付。依赖步骤：S09、D07审查。**
     - 输入：已封存结果、模型引用、科学状态、数据/代码依赖。
     - 前置条件：所有门禁有效；D08 final文件不存在。
     - 具体操作：A0先写README说明重放命令、训练身份、失败门槛和科学限制；运行accept仅生成stages/D08/acceptance.json与source_dependencies.json；A0根据验收结果写final_report，再生成final_manifest，最后写D08实施回执。accept不得写final_report、manifest或回执；A4后置审核不回填前述文件。不重新训练或重跑整个测试研究。
     - 工具或命令模板：README完成后运行`uv run --no-sync python -B "$SCRIPT" accept --run "$RUN"`；A0随后按第7节顺序生成报告和清单。accept只校验已有结果/模型重放，不启动fit。
     - 预期产出及保存位置：`stages/D08/acceptance.json`、`source_dependencies.json`、`README.md`、`final_report.json`、`final_manifest.json`。
     - 完成判定：第10节所有完整性项PASS；科学门槛保留真实正/负结果；可重放研究模型存在；主结论与12单元一致。
     - 验证方法：A4独立核对主结论和全部登记摘要，核实72+≤3次fit上限与无测试后调参；不把代码读过写成fit重现。
     - 失败处理：完整性验收FAIL即停止；NEGATIVE_RESULT且证据完整为正常完成。A4拒绝后不能改写原D08，等待高级规划者给新的附加修复方案。
     - 禁止事项：改旧D08、修改trusted、发布HTML/部署/制造、宣称范围外物理验证通过。
- 完成标准：完整性自动验收PASS，研究结论自包含，依赖哈希及复算范围可追溯。
- 回执：A0写receipts/D08.json，包含final_manifest的SHA256；回执之后只允许A4写review/D08。
- 独立审查：A4写后置review/D08/receipt.json，引用实施回执和manifest的hash；PASS时released_next_stage=null，不回填原封存文件。

## 6. 给实现者的接口合同

### 6.1 代码边界与输出

`SCRIPT` 用 argparse 接受：`train/select/evaluate/physics/accept --run ABS_RUN`，另有 `predict --run ABS_RUN --input ABS_CSV --output ABS_CSV`。predict是交付后的使用接口，本计划D08只在内存重放已有test坐标，不接受用户未登记的新文件。

每个命令加载D02/protocol.json与输入hash，消费前序回执/审核门禁。另实现仅供主进程使用的内部子命令`_train_worker/_evaluate_worker --run ABS_RUN`：公开train/evaluate分别用`[sys.executable, SCRIPT, 内部子命令, "--run", RUN]`启动一个worker并执行固定超时；内部命令执行相同门禁，顺序计算，不递归启动worker或并行训练池。完成每个fit后ledger即时写入一次，不轮询。A0/A1不得直接调用内部子命令绕过超时。

输出目录与命令一一对应；目录里已有该命令产物就拒绝覆盖。launcher日志不算预先存在的研究产物。不得支持 `--force`、`--skip-checks`、`--allow-ood` 或隐式resume重训。先写同目录临时文件，成功关闭后rename；最终文件不可覆盖。预测/指标按scope、target、candidate或预测角色、structure_key排序。

训练frame构造：从标签中按 scope/role/target筛行，将 `median` 改名为target，保留模型类需要的几何/branch列。fit函数不接受整个标签表或test ID；调用前断言frame身份精确等于本单元train ID。验证只传几何/身份到predict，真值在外部评分函数中匹配。

逐点表最低列：`scope,target,prediction_role,candidate_id,structure_key,p3_design_group_id,p3_branch_id,truth,prediction,error,abs_error`。训练/验证candidate逐个保存；测试prediction_role固定selected/C00/C01。ledger每fit含scope/target/population/candidate、train ID列表及hash、config、起止时间、模型hash、每branchtransform_records；常量记录train统计来源。

主研究的fit调用与输出只来自train命令。敏感性fit只来自evaluate专用分支。select/physics/accept/predict严禁调用fit。训练bundle包含实例、配置、训练身份、源hash和包版本；不假称为脱离BE源码可独立部署的安装包。

### 6.2 范围检查与错误

predict只使用 group_holdout 每target selected 实例。输入必须含 `eg_um,fl_um,area_um2,has_pf` 和唯一 `query_id`；has_pf严格0/1，几何有限、EG/FL≥0、area>0。branch=`pf{has_pf}_eg{int(EG>0)}_fl{int(FL>0)}`。

- 非法输入：该行 `INVALID_INPUT`，四预测null。
- 未见分支：`UNSUPPORTED_BRANCH`，四预测null。
- 任一坐标不在该branch train闭区间内：`OUT_OF_TRAINING_DOMAIN`，四预测null；不得钳制坐标。区间比较不扩展容差。
- 其余：几何标 `IN_TRAINING_CONVEX_HULL`；返回四个研究预测与各target研究门槛状态、trusted=false。正值/耦合范围不合格时target状态`NUMERIC_PHYSICS_CHECK_FAILED`，保留原有限值和失败标记。

之所以可用轴区间替代凸包判定，是D02强制每分支训练包含所有几何角点，且area的log单调；若该条件不成立，不能继续此接口，也不能自行改用外推。

执行错误命令退出2，完整性错误3，超时124，正常完成0；科学负结果仍退出0并在结果字段明确记录。不能吞异常转PASS。输入schema错误、源hash不符、输出冲突是执行错误；源码封存hash不符是完整性错误。

### 6.3 唯一针对性测试的固定内容

测试用合成元数据/常量模型桩验证机制，不用断言真实训练一定高分：

1. 两结构同组不能跨role；故意给同组不同role必须报错；重复device ID跨角色必须报错。
2. 训练frame混入一个test ID必须在fit前报错；记录型模型桩证明fit只收到train。
3. 候选验证MAE并列时按固定顺序；故意改变test真值不改变selected。
4. 修改源hash或冻结代码hash、重复输出目录必须失败；不能创建覆盖文件。
5. 给train缩放范围[0,1]和validation/test极端值，确认记录的缩放只由train决定。
6. 两分支、每组两结构的固定小表，验证bootstrap每次抽整组和分层组数不变；手算MAE/delta与实现一致；固定seed可重现。一个组的分支必须标小样本。
7. 未见branch、负几何、范围外值返回第6.2节指定状态且预测null；边界闭区间允许；不得隐式裁剪。
8. C01_MAE=0时不除零；所有候选均无改进时研究状态NEGATIVE_RESULT、执行完整性仍可PASS。
9. accept/select/physics/predict路径替换fit为“调用即抛异常”桩，验证这些路径不触发拟合。

不能为上述测试引入新依赖。A4复核的是这些行为，不要求任意覆盖率百分比。

## 7. 最终清单与非循环哈希

final_manifest.json列出：PLAN/PROMPTS/四个源依赖引用，以及RUN内代码、协议、划分、模型、预测、指标、策略、报告、验收结果、D01–D07实施/审核回执。每项含绝对路径、bytes、sha256、artifact_role；源引用标只读依赖，不能冒充本轮生成。accept只生成验收结果及依赖表；final_report/manifest/实施回执均由A0按规定顺序写出，不能由accept预先创建。

生成manifest时排除自身、receipts/D08.json、review/D08/**、未封存临时文件。顺序固定：其他产物 → final_report（注明pending final A4）→ final_manifest → receipts/D08引用manifest摘要 → review/D08引用前两者摘要。A4签收是后置证据，不反向改final_report、manifest或D08回执。最终用户报告可以说“已获后置A4 PASS”，须同时链接后置回执。

final_report最低状态：`digital_work=COMPLETED`、`study_outcome=IN_DOMAIN_RESEARCH_SUPPORTED|NEGATIVE_RESULT`、12单元真实结果、`scientific_status=RETROSPECTIVE_ONLY`、`prospective_validation=NOT_RUN`、`simulation=NOT_RUN`、`frame_quantitative_validation=NOT_EVALUABLE_WITH_CURRENT_INPUTS`、`legacy_d09=NOT_RUN`、`legacy_d10=NOT_RUN`、`trusted=false`、`manufacturing=NOT_AUTHORIZED`、`deployment=NOT_AUTHORIZED`。新性能候选搜索未执行；旧性能候选为0。不能将未搜索写成“本轮证明候选为零”。

## 8. Frame物理检查的确定内容

使用以下一手来源，不需要执行者自行寻找物理定律：

1. Pensala & Ylilammi, 2009, *Spurious Resonance Suppression in Gigahertz-Range ZnO Thin-Film Bulk Acoustic Wave Resonators by the Boundary Frame Method: Modeling and Experiment*, DOI 10.1109/TUFFC.2009.1238。[作者机构记录](https://cris.vtt.fi/en/publications/spurious-resonance-suppression-in-gigahertz-range-zno-thin-film-b/)。这是特定ZnO结构证据，不能直接当本项目AlN数值真值。
2. Pensala, 2011, *Thin film bulk acoustic wave devices: performance optimization and modeling*, VTT P756，§5.2、§5.5，印刷页50–69，图39–43。[作者学位论文](https://publications.vtt.fi/pdf/publications/2011/P756.pdf)。宽度有最优条件；Type-I示例中frame可作为传播/匹配区，外区倏逝；AlN常见Type-II不能照搬这一构造。
3. Li et al., 2018, *Use of double-raised-border structure for quality factor enhancement of type II piston mode FBAR*, DOI 10.1007/s00542-018-3938-7。[出版方摘要](https://link.springer.com/article/10.1007/s00542-018-3938-7)。适当边界结构能减少模态转换、增强反射并提高反谐振Q；本计划只使用可访问摘要，不声称读过付费全文。
4. Yang Wenxia, 2013, *Investigation of spurious waves in thin-film bulk acoustic wave resonators*。[作者学位论文机构记录](https://ira.lib.polyu.edu.hk/handle/10397/83907)。特定frame-like airgap研究区分寄生波激发与向被动区能量泄漏。

physics_policy必须逐项包含下表，不自行追加数值物理约束：

| 检查ID | 本轮处理 | 可证明/不可证明 |
|---|---|---|
| P01_FINITE_UNITS | 检查有限性、单位、合法几何 | 排除非法输入，不能证明声学结构身份 |
| P02_SCALAR_BOUNDS | Q>0，0<k2eff_pct<100；失败记录，不裁剪 | 排除明显标量异常，不能证明被动性/能量守恒 |
| P03_BRANCH_DOMAIN | EG/FL零态分支隔离，范围外默认拒绝 | 防止无声明跨拓扑/域推理；不能保证域内误差 |
| P04_FRAME_MAPPING | NOT_EVALUABLE_WITH_CURRENT_INPUTS | FL物理定义、真实frame宽/高/材料与边界缺失；禁止等同 |
| P05_LATERAL_CONFINEMENT | NOT_EVALUABLE_WITH_CURRENT_INPUTS | 标量表无位移/应力/速度/能流场，不可计算横向限制能力 |
| P06_COMPLEX_IMPEDANCE | NOT_EVALUABLE_WITH_CURRENT_INPUTS | 当前模型无复阻抗输出，不能做被动性/因果性或复曲线验证 |
| P07_OOD_ACCURACY | NOT_VALIDATED | 无新增范围外实测；物理相容只是必要筛查，不是精度证明 |

frame_physics.md必须区分三种机制：界面模态反射/透射/转换；区域传播或倏逝及匹配；横向驻波与电激励耦合。不能把“frame区域必为倏逝”当普遍事实，也不能从Q增加唯一归因于声能被限制。

后续范围外物理研究的输入清单固定为：带尺寸的实际版图与截面、层厚/材料/晶向、frame与外区/支撑构造、频带/模态定义、损耗模型、边界与参考面、独立几何坐标映射。它们不在本轮要求用户补充。

若将来输入齐全，再由高级规划者另立计划：求各区域色散和复横向波数、模式能流归一化散射及转换、空间能量分布/外泄功率和模态耦合。对均匀倏逝区域可用场幅 `exp(-κx)`、能量量级 `exp(-2κx)` 描述衰减（κ>0，长度单位一致）；不得在未知κ或frame支持传播时套用。具体数值阈值、模式选择与损耗分解属于**需要高级模型决策**，本轮不推断、不调用求解器。

## 9. 分支、停止、恢复和禁止事项

### 9.1 决策表

| 观察条件 | 唯一动作 |
|---|---|
| 执行模型尚未复述或高级复述审查未PASS | 只完成复述，不创建RUN，不训练 |
| 当前阶段双PASS且released_next_stage正确 | 自动进入指定下一阶段，不逐步询问用户 |
| 物理输入缺失，但D02标签/身份/版本有效 | 继续统计训练；D07记未评估，不转回COMSOL资料搜集 |
| 模型无增益、常量胜出、科学门槛FAIL | 保留结果，正常完成D07/D08；负结果也是合法结局 |
| 后台尚无退出码且仍有独立工作 | 做独立工作；不轮询、不启动第二个任务 |
| 独立工作已完且一次检查仍无退出码 | 一次报告PENDING和接续入口，结束当前回合；不虚报失败 |
| 命令非零、超时、源/封存hash改变、越界写入、A4 FAIL | 停止依赖链，保存事实，交高级规划者局部重规划 |
| 仅D03首次针对性测试失败 | 允许一次明确局部修复和重跑；第二次失败停止 |
| 独立审查工具不可用/身份无法证明独立 | BLOCKED；不得自审或把模型自检当A4 |
| 其余未覆盖情形 | 停止该步骤，报告NEEDS_REPLAN，不自行猜测 |

对命令失败的通用上限：同一命令连续失败2次必须停；本计划大部分阶段更严格，只运行一次。此上限不是授权任意重试一次。不得通过换参数、换目录、换命令包装、换代理名称绕过次数。

失败报告只传事实：plan_id/version/hash、stage/step、实际argv/cwd、退出码、原始报错、已写文件及hash、通过/失败断言、是否启动后台任务、所需决策。不能自己更改计划或重新开attempt。需要重规划时高级模型输出完整新版受影响合同、修复写入边界和最早重跑阶段；已封存失败记录保留。

没有破坏性写入，因此不需要复制整个仓库作备份。回滚方式是停止、保留本轮新目录为失败证据；禁止删除新目录掩盖过程，更禁止恢复/清理旧用户文件。

### 9.2 中断后的唯一接续算法

接续不是重新执行D01。外部续接消息到达后，A0按以下顺序只读判断一次：

1. RUN不存在：仅当复述审查PASS且绑定同一PLAN时，从D01开始。
2. RUN存在：必须找到D01/preflight和已登记的相同plan_id/plan_sha256；不一致或缺失则停止NEEDS_REPLAN，不重新初始化。
3. 从D01顺序核验已存在实施/独立回执及摘要。任一FAIL、摘要冲突、阶段跳号或实施回执存在但内容无法核验，立即停止。
4. 某阶段实施PASS但尚无独立回执：只派发该阶段A4；不重做实施。
5. 当前阶段D04/D06有launch而无实施回执：只按第4.3节一次收集退出码/日志。退出码0则检查已生成产物并写原本尚未写出的实施回执；非零停止；无退出码按PENDING分支。绝不重复fit。
6. 当前阶段无launch/无实施回执但存在未封存研究输出：视为中断的不完整现场，停止交高级规划者；不自动删除、补写、续训或覆盖。D03首次失败的局部修复仅在同一次实施中的已登记失败分支适用，不作为未知现场的修复许可。
7. 下一阶段尚无研究产物且前序双PASS完整：执行该阶段；D08已经双PASS则只返回交付链接，不再运行任何计算。

本算法不能用来主动定时轮询；只由真实完成事件或外部续接消息触发。需要高级模型修订时，只有它明确登记的版本/哈希迁移规则可改变绑定，执行者不能自己接受新版本。

### 9.3 禁止事项

- 不许重复打印未变化的状态；不许轮询等待、循环sleep或反复续接后台句柄。
- 不许修改非本阶段负责的文件；阶段产物/回执封存后不可覆盖。
- 不许改旧 B/D/P13/postseal、旧D04 FAIL、旧D08、旧性能候选、trusted、协议或回执；不新建替代旧attempt。
- 不许全局pip install、更新依赖、改现有dirty worktree、提交推送、生成替换桌面HTML。
- 不许重解压/重哈希大归档、不许恢复缺席器件或补造历史去嵌配置。
- 不许随机拆器件、用最优器件替代结构中位数、按Fail名删数据、为匹配预期删除FL=0高Q标签。
- 不许测试集选模、test后调参、换seed、无预算搜索、擅自重训全量模型。
- 不许将GP配置差异当独立方法、把统计置信区间当范围外预测区间。
- 不许运行COMSOL、制造、部署、外部实验或等待本轮不会提供的范围外实测。
- 不许把规划值写成实际结果、把读过写成复算、把存在的文件写成历史使用证明。

## 10. 最终验收清单

| 验收项 | 对应目标 | 检查方法 | 通过标准 | 必须保留的证据 |
|---|---|---|---|---|
| 输入真实且保护旧结果 | 可信研究起点 | 重哈希第2.1节四源；核对写入目录 | 摘要一致、无旧文件写操作 | source_manifest、命令日志、前后源摘要 |
| 主划分可重建 | 明确train/validation/test | 独立重建排序与anchor | 202/43/43组，404/86/86结构，6分支覆盖 | split_manifest/summary、独立复算结果 |
| 无身份泄漏 | 防止重复器件/配对结构泄漏 | scope内结构/组/器件集合交集 | 交集空，四目标role一致 | data_checks及A4复算 |
| 水平留出明确 | 检验EG/FL内部水平泛化 | 检查scope/role各水平集合与角点 | 与第3.3节完全一致 | 划分清单及坐标检查 |
| 数据口径不变 | 所有合格器件中位数 | 标签hash、资格与frame构造审查 | 无重聚合/删标签/器件加权 | 标签引用、fit身份ledger |
| 训练有限且可追溯 | 实际训练完成 | fit ledger/模型文件/配置 | primary72次，敏感性≤3，无额外fit | 模型、ledger、运行日志 |
| 验证选模与测试隔离 | 可解释研究评价 | 独立MAE选择、比较冻结hash及时间 | 12选择一致；D05封存先于D06 | selected、test_freeze、门禁回执 |
| 模型可重放 | 可使用研究模型 | load实例、重放全部selected测试几何 | 与记录误差≤1e-10+1e-10×绝对值 | replay检查及源码/版本依赖 |
| 指标与区间正确 | 量化误差 | 独立逐点/簇bootstrap复算 | 第3.5节公式和seed匹配 | predictions、metrics、intervals |
| 科学结论诚实 | 正/负结果都可交付 | 重算门槛及主结论 | 12单元全列；无隐藏失败 | scientific_result、final_report |
| 稀疏分支未夸大 | 说明证据覆盖 | 分支组数与标记 | <10组均标LOW_TEST_GROUP_COUNT | branch_metrics、README |
| Qp敏感性分开 | 保护主标签口径 | 比对选中配置/身份/native/common | 不替代primary，不重新选模 | sensitivity ledger/对照 |
| 物理与范围外边界 | 为以后外推留确定入口 | 检查第8节七项状态及代码拒绝 | frame未评估、OOD拒绝、无伪物理保证 | physics_policy、说明、测试 |
| 独立门禁完整 | 防止自报通过 | 逐阶段查身份/摘要/释放 | D01–D08独立PASS，末阶段next=null | 全部实施/审核回执 |
| 哈希不循环 | 可追溯交付 | 检查manifest排除项及引用图 | 排除自身及后置D08回执/审核 | manifest、D08两回执 |
| 无越界资格提升 | 科学状态受限 | final_report固定枚举 | trusted=false、legacyD09/D10=NOT_RUN、未仿真/制造/部署 | 最终报告及A4检查 |

自动完整性验收可以PASS且study_outcome=NEGATIVE_RESULT；“所有验收通过”不意味着模型一定优于基线。accept发生在最终报告/manifest/D08回执生成之前：它核验D01–D07和已有研究产物；将“独立门禁完整”的D08部分、“哈希不循环”的最终封存部分、“无越界资格提升”的final_report部分统一标`PENDING_EXTERNAL_REVIEW`，明确列出尚未生成的文件，不得伪造检查通过。其余机器项必须PASS。随后A0按第7节顺序封存，A4独立用程序复哈希清单、检查引用图和状态枚举并核对D08回执，完成这三个延后项。只有机器项PASS且后置A4将所有延后项核为PASS，才可报告最终完成；不回填或重跑accept形成循环。

## 11. 给执行者的总指令

先只读复述，交高级规划者检查；只执行复述审查绑定的最终版本。随后从D01开始按依赖顺序执行。每步只做计划写明的事；参数、角色、门槛、输出、失败分支以此冻结规格为准。双PASS后自动推进，不一步一问。允许的并行仅为角色表明确的准备/实现/审查任务，禁止并发写同一文件。

计划未写的判断一律停下来交高级规划者，不自行优化或补事实。不要把正常负结果变成新一轮调参。全部目标和完整性验收完成后立即交付；不追加候选搜索、COMSOL、部署或实测任务。每步只报告：步骤编号、DONE/FAILED/BLOCKED、实际执行、实际产出、验证证据、与计划偏差、下一步或需要的决策；状态未变不重复发送。
