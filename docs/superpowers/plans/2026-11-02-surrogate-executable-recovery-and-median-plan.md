# P13 交付修复与全部器件中位数训练：完整执行计划

日期：2026-11-02。状态：**计划已编写，执行尚未开始**。
适用对象：接手本项目的编码代理。按任务ID顺序执行，不自行跳过验收或补造通过记录。
本文件展开 `2026-11-02-surrogate-next-steps-and-median-policy.md`；既有冻结科学协议和历史回执保持不变。

## 0. 要完成什么

分两阶段，分别验收，分别报告：

- **阶段A：完成P13负结果交付。** 解决文件归属、修正O15的JSON空值语义、重新验收、正式封存O16。科学状态仍为REJECTED，推荐数0。
- **阶段B：建立并验证新版本“全部器件中位数”训练链。** 从器件记录重新聚合，完成数据检查、固定模型的回顾性验证及独立报告。不覆盖旧模型，不将结果写回P13，不默认启动新候选搜索。
- **阶段C：独立新结构实测。** 只交付测量协议草案；没有新实测数据时状态为NOT_RUN，不伪造已完成。流片、外部测量或部署不属于本计划自动执行范围。

用户当前要求是扩展计划；本轮不执行A/B。后续收到“按此计划执行”即可从A00依次执行到B07；无需对常规读写、修复和测试反复询问。只有身份/来源无法判明、缺失必需输入、依赖不可用或新增不可逆外部动作时请求具体信息。

**“无脑执行”指每步都有确定操作和失败分支，不代表忽略门禁或在证据不足时宣布成功。**

## 1. 固定路径、环境与前置事实

以下变量仅为任务变量，不改写HOME/CODEX_HOME或全局配置：

```bash
export ALN_MAIN='/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master'
export ALN_ML='/Users/jingbozuo/Projects/aln-data-backend-ml'
export ALN_BE="$ALN_ML/backend"
export ALN_P13="$ALN_BE/surrogate_data/optimization_p13_20261027_r01"
export ALN_REC="$ALN_P13/resume_20261029_r01"
export ALN_VIEW="$ALN_ML/frontend/optimization/p13_failure"
export ALN_DOC="$ALN_MAIN/docs/superpowers/plans"
export PYTHONDONTWRITEBYTECODE=1
export GIT_OPTIONAL_LOCKS=0
```

工作目录要求：Python命令在ALN_BE下以`uv run --no-sync`运行；页面命令在ALN_VIEW运行，使用已安装的frontend/node_modules工具。禁止全局pip、uv sync、npm install、自动下载浏览器、更新锁文件。不要运行旧训练脚本来修复交付。

本次编写计划时核对的基准（执行时须重新确认，不能把本表当作实时通过证据）：

| 对象 | 已核对状态或SHA256 |
|---|---|
| V18 `verification/test_report.json` | PASS；a3871e13d17d6600b861edf7d9835bd4ea9d8edc20a88986462ee865db3f72e7 |
| G7 `REC/verification/final_manifest_check.json` | FAILED；a37204ce098a49732efe12384ff54a8ccd4abb9c9ad8b72db1eea52b1ff44524 |
| O14 `delivery/candidates.csv` | 32eedf2acc5c54508d16b41ab79522df1d6ebcca95648580d38f627d404fc539 |
| O14 `delivery/metrics.csv` | 10b281c30b530b62d8d34ba8361175031133a09d70cf4d8fc137d0f14175fbb7 |
| O14 `delivery/decision.json` | b93a563a354d7914db7755a9328b1367fd82547130e61b1f5afcc3f49692bf76 |
| O15已确认契约缺陷 | 490个应为null的字段实例为字符串空值；全JSON空字符串共589个 |
| 正式O16 | final_report.json、delivery/README.md、final_artifact_manifest.json均尚未生成 |
| 科学状态 | REJECTED；eligible_models=[]；recommendation_count=0；trusted=false |

首读文件，按顺序：当前AGENTS.md/CLAUDE.md；`2026-11-02-surrogate-p13-execution-summary.md`；本计划；`2026-10-29-surrogate-p13-post-review-execution-order.md`；`REC/contracts/failure_delivery_contract.md`和schema；`2026-11-02-fl0-audit-owner-declaration.json`。

## 2. 角色、写入权限与调度

执行阶段沿用既有计划要求的独立子代理核验，最多同时4个代理。角色名不是模型身份；记录实际agent ID，不能读取模型身份时写无法验证，不补造历史身份。

| 角色 | 工作 | 可写范围 |
|---|---|---|
| A0 主控 | 快照、归属处置、调度、候选清单、最终提升 | 新恢复attempt目录、正式O16指定路径、新计划/总结 |
| A5 页面实施 | 空值修复、类型/渲染/导入契约、构建 | ALN_VIEW；签收后的O15指定文件；自身attempt |
| A4 独立验收 | 归属/软件/清单核验 | 自己的新verification attempt、检查脚本/日志；不得修改被审查对象 |
| A1 数据及训练 | 阶段B数据聚合、模型适配、实验 | B阶段独立目录及登记的新模块/测试 |
| A2 科学复核 | B阶段目标口径、划分、结论复核 | B阶段review目录；不得修改训练输出 |

阶段A：A0+A5+A4；阶段B：A0+A1+A2+A4。A4指出实现问题后交回原作者修复，不一边修复一边签独立PASS。每个路径只有一个写入方。阶段A与B串行。

A阶段只读：O00–O14、V17、所有历史attempt、原始数据、模型、旧页面、FL0四个脚本与其审计结果。旧V18/O15在新版本替换前必须完整归档。原GR0维持BLOCKED。

## 3. 统一执行与记录协议

### 3.1 attempt分配

A00先扫描现有目录，再选未占用目录：`ALN_REC/recovery_20261102/attempt_NNN`，从001递增；下面简称**A_RUN**。只有不存在时才创建，使用`mkdir(exist_ok=False)`；不能清空旧目录。阶段B独立根为`ALN_BE/surrogate_data/optimization_device_median_v1/attempt_NNN`，简称**B_RUN**，同样分配。

每个任务保存`<RUN>/<task_id>/receipt.json`，最低字段：

```json
{
  "task_id": "A00", "attempt_id": "attempt_001", "status": "NOT_RUN",
  "owner_agent_id": null, "reviewer_agent_id": null,
  "inputs": [], "outputs": [], "source_files": [],
  "commands": [], "checks": [], "started_at": null, "finished_at": null,
  "scientific_status": "REJECTED", "limitations": [], "next_task": null
}
```

每项文件记录绝对路径、字节数、SHA256；每条命令记录argv数组、cwd、环境差异、起止时间、退出码、完整日志路径与哈希。用subprocess数组执行，避免shell拼接。stdout/stderr保存到本attempt。检查状态只允许PASS/FAILED/BLOCKED/NOT_RUN；被跳过的必需检查不能当PASS。

清单不可包含自己的最终哈希；由下游回执引用其哈希。独立核验回执位于被核验清单之外。任务开始前与结束后复查保护文件，排除仅限事先登记的精确生成路径，不能排除整个scripts目录或整个worktree。

### 3.2 失败、重试与中断

确定性代码错误：保留失败日志，修最小根因，新attempt重试；同一原因连续两次仍失败，输出阻塞原因、受影响任务、已尝试措施和最小所需信息，不无限重试。科学门槛失败是合法实验结果，不追加模型、种子或删样本追求PASS。

中断恢复：从最后一个输入/输出哈希仍匹配的PASS任务继续；变更上游后使其所有依赖任务失效。不能仅看到文件存在就跳过任务。

## 4. 阶段A任务表

| ID | 主责→验收 | 前置 | 结果 | 失败去向 |
|---|---|---|---|---|
| A00 | A0→A4 | 无 | 状态快照、写入清单、attempt分配 | 只读查明差异 |
| A01 | A0→A4 | A00 | FL0归属签收和限定恢复回执 | BLOCKED，不修改O15 |
| A02 | A5→A4 | A01 | 新attempt构建/验收入口及历史备份 | 修复路径与保护逻辑 |
| A03 | A5→A4 | A02 | null契约回归测试先失败 | 测试未检出旧缺陷则重写测试 |
| A04 | A5→A4 | A03 | 修复生成链，新O15及T16回执 | 保留候选，不发布 |
| A05 | A4 | A04 | 新T18/V18软件验收 | 退回A04新attempt |
| A06 | A0→A4 | A05 | T19候选、自检和独立G7 | 不提升，修具体失败项 |
| A07 | A0→A4 | A06 G7 PASS | 正式O16、逐字节提升回执 | 停止并保留部分写入记录 |

### A00：只读预检与保护快照

1. 记录两仓库HEAD/分支/status、未跟踪文件、依赖版本和解释器路径。ML预期分支`feat/backend-ml-surrogate`；不符则核查，不自动切换或reset。
2. 读取旧O00/H00/H01/H05、O01与冻结清单，逐项检查哈希。原保护项目仍保护；不能用新快照把未知差异洗白。
3. 核查上节基准、正式O16是否出现、新并行文件是否出现。若已由其他任务完成交付，先独立读验其回执，避免再次覆盖。
4. 创建A_RUN，仅写快照、诊断和精确权限表。将本计划、归属声明及本次新文档登记为当前规划任务归属，不将它们当科学输入。
5. 输出`A00/snapshot.json`、`protection_diff.json`、`write_scope.json`、`environment.json`及receipt。

可直接运行的只读命令：

```bash
git -C "$ALN_MAIN" status --short
git -C "$ALN_ML" status --short
git -C "$ALN_ML" branch --show-current
cd "$ALN_BE"
uv run --no-sync python --version
uv run --no-sync ruff --version
node --version
```

PASS：所有输入存在；差异完整列出；没有靠删除/恢复/覆盖未知文件消除差异。缺依赖时记录缺项，不安装。

### A01：签收FL0归属并恢复限定权限

输入：MAIN文档`2026-11-02-fl0-audit-owner-declaration.json`、本聊天实际创建记录、其引用的FL0 receipt/validation/manifest/README。

逐项验证4个脚本当前SHA256等于声明，当前字节等于归档副本，证据文件哈希一致；主审计脚本应为`4a969781bbda8c5ba2f19f0904b92309d34b19abb606408bd5aa59e1409fac8e`。对目录其余新增项按审计manifest验证，不能只认文件名或时间戳。脚本所有者的声明不能充当A4的独立核验。

输出`A01/ownership_disposition.json`、`independent_owner_check.json`、`resume_scope.json`。仅追加到既有归属链，不改写旧H01/旧失败G7。A4签收时明确“FL0任务拥有；保留；P13不写入；没有未解释的范围内差异”。仅恢复A02–A07权限，原GR0仍BLOCKED。

### A02：先解决旧入口覆盖与硬编码

先将当前ALN_VIEW全部源码、O15全部文件、V18/T18清单、G7及T19候选/相关清单复制到`A02/before/`，保存逐文件哈希。旧artifact清单引用的全部文件必须可定位，不能只备份报告JSON。

已确认的风险入口：

- `ALN_VIEW/scripts/build.mjs`：输出viewer路径及attempt_002写死；构建会直接覆盖O15。
- `scripts/verify.mjs`、`scripts/finalize.mjs`：旧工作目录/回执引用需要核查；finalize中有25个Node测试及3个focused Playwright的精确数量。
- `playwright.config.ts`及浏览器tests：旧报告路径与输出路径需要核查。
- `P13/verification/t18_work/attempt_002/*.py`与`t19_work/attempt_001/{build_final,self_check}.py`：历史脚本只读，不直接执行到旧目录。

实施要求：给页面构建/核验脚本增加一致的显式参数或环境入口：`ALN_P13_VIEWER_OUT`、`ALN_P13_TASK_WORK`、`ALN_P13_TASK_ATTEMPT`；浏览器测试读取同一viewer路径，trace/output位于当前attempt。恢复模式缺参数时拒绝写入。未知参数拒绝，输出不得指向旧attempt。保持冻结输入读取路径不变。T18/T19在新attempt复制并改造入口，不能修改历史脚本。

这些环境变量是**待A02实现的新接口**，本计划编写时尚不存在；实施并测试后才可使用下文命令。不要声称已有一键恢复脚本。

固定测试数改为：原有测试ID全覆盖，加新增回归测试；实际总数从新日志解析，不把新增数量硬改成通过。旧3个focused用例仍需覆盖，但不能冒充完整T18的7个用例。

输出`A02/entrypoints.json`（新路径、参数、写入边界、哈希）、`backup_manifest.json`。A4确认不会覆盖旧attempt、不会读旧命令回执、运行前验证冻结输入。

### A03：空值契约回归测试（先红）

必须修改/新增ALN_VIEW/tests中的构建、导入校验、静态及浏览器用例。字段白名单精确为：

| 对象 | 应为null的字段 | 当前实例数 |
|---|---|---|
| 每个候选 | conservative_value、error_margin、extrapolation_distance、testable_max_distance、neighborhood_result | 20×5=100 |
| value=null的指标行 | source_file_id、source_sha256、source_row_key、scope、support_label | 78×5=390 |

`grade`和`value`等已按契约为null的字段仍应检查。其他空字符串逐字段依H04判定，输出`A03/null_field_inventory.json`，不能断言其余99处必然合法，也不能全局替换。

测试矩阵：旧数据明确失败；正确null通过；缺键、空字符串、字符串"null"、字符串"NaN"或非JSON数值按字段规则拒绝；合法0/"0"按现有完整精度表示保留；有来源值的82行来源不丢失；缺失78行原因非空；HTML内嵌JSON与旁置JSON等价；导入失败保留当前页面；伪造等级/来源/推荐/顺序被拒绝。新增浏览器导入空字符串反例。

输出真实red日志及退出码。只有旧缺陷能被测试捕获后进入A04；不能修改H04来使旧实现通过。

### A04：修复生成器并重建O15（绿）

修改`build.mjs`的CSV→JSON边界，按字段名单转换空值，保持CSV、有效数值的完整精度字符串及候选顺序不变。同步`src/types.ts`、`validation.ts`、必要的app渲染和测试fixture，避免`value || null`和`String(null)`。

新O15写到`A04/viewer/`；新T16工作目录`A04/work/`。显式设置A02接口后执行：

```bash
cd "$ALN_VIEW"
export ALN_P13_VIEWER_OUT="$ALN_A_RUN/A04/viewer"
export ALN_P13_TASK_WORK="$ALN_A_RUN/A04/work"
export ALN_P13_TASK_ATTEMPT="$(basename "$ALN_A_RUN")"
npm run compile
npm run build
npm run build:check
npm test
node scripts/verify.mjs
node scripts/finalize.mjs
```

`ALN_A_RUN`由A00填写实际绝对目录并export；先创建不存在的A04/viewer与A04/work目录。verify/finalize必须已适配新回执；不能先运行再发现覆盖旧证据。每条命令单独记日志和退出码，非零即停止后续命令。

A4独立检查：违规490处归零，所有H04字段语义正确；20候选、160指标行、82有值/78缺失、17正常历史对照+3负面对照不变；C19同结构匹配不变；O14哈希无变化；科学状态和推荐不变。新O15二次构建逐字节一致。

本步骤只产候选，不直接替换正式viewer；后续验收应指向候选。所有清单记录候选路径与计划提升路径，防止提升后清单仍无法定位文件。

### A05：重新执行完整T18

A4从旧V18的commands提取必要检查，改造到`A05/`并独立运行；不是复制旧PASS。最低检查：

- Ruff：原19个检查文件及本轮相关新Python文件，0错误；历史文件只检查不格式化。
- 完整`tests/ml`：原265个用例覆盖不能少，新增测试另计；0失败、0必需用例跳过。
- 严格TS、全部Node契约测试、全部原7个Playwright场景及新增空值用例。
- 原44项浏览器断言逐项保留，新增null断言；离线无外部请求；390px无横向溢出；失败导入不污染当前状态。
- O14与O15的数值、来源、顺序、缺失语义一致；原25项数值核验覆盖不能删除。
- 保护清单与依赖锁文件不变；新增回执引用新O15哈希。

已有真实命令形态（A_RUN及输出目录由执行器传入）：

```bash
cd "$ALN_BE"
uv run --no-sync pytest -q -p no:cacheprovider --basetemp "$ALN_A_RUN/A05/pytest_tmp" --junitxml="$ALN_A_RUN/A05/pytest_ml.xml" tests/ml
cd "$ALN_VIEW"
../../node_modules/.bin/tsc -p tsconfig.json --outDir "$ALN_A_RUN/A05/tsbuild"
node --test "$ALN_A_RUN"/A05/tsbuild/tests/*.test.js
../../node_modules/.bin/playwright test -c playwright.config.ts --output "$ALN_A_RUN/A05/playwright"
```

A05运行前保留 `ALN_P13_VIEWER_OUT=$ALN_A_RUN/A04/viewer`，将 `ALN_P13_TASK_WORK` 改为 `$ALN_A_RUN/A05`，将任务ID登记为T18；不得让A05写回A04命令回执。测试文件里的输入路径也须读取A02参数。不可只改变trace输出而仍测试旧正式HTML。Ruff文件列表以旧V18 argv去重加本轮新文件生成，不做`ruff --fix`。

输出`A05/test_report.json`、`t18_artifact_manifest.json`、JUnit、逐项浏览器检查、截图/trace、数值差异表、保护检查、命令回执。只认本次执行的实际计数。

### A06：T19候选与独立G7

A0在`A06/candidate/`生成O16和T19清单，复用旧build_final/self_check逻辑的只读参考，新增独立副本并显式绑定本次O15/V18路径与哈希。不得修改旧attempt或依赖旧V18的PASS。候选至少包含：

1. `verification/final_report.json`；
2. `delivery/README.md`；
3. `delivery/final_artifact_manifest.json`；
4. `verification/t19_artifact_manifest.json`。

报告分别列execution/delivery/scientific状态，明确“当前证据不支持k²提升1个百分点”。T19自检通过后由A4独立G7，不能把candidate self_check 701/701当作独立G7。

G7检查：旧1673项检查中的适用项目不减少；F001所有null语义已修复；F002归属已独立签收；新增改动无未知保护差异；清单路径存在且哈希一致；O14和冻结科学输入未变；本次软件门禁全部通过；空推荐与REJECTED明确。计数变化说明原因，不写死必须仍为1673。

清单图：上游输入→O15/V18→T19候选清单→独立G7回执→提升回执。G7回执不能加入它自身校验的清单导致循环。

### A07：正式提升与阶段A收尾

前置：本次独立G7=PASS且输入/候选哈希仍匹配。A0生成promotion_map，列明候选→正式路径和预期SHA256，包括O15、V18及正式O16。旧O15/V18/清单已在A02归档，覆盖前再次确认未被其他任务修改；无匹配则停止，不能覆盖并行更新。

按map逐文件原子写入/替换，记录开始、已写文件、最终读取哈希；中途失败不称交付PASS，不自动回滚其他任务修改。候选文件保留，使原清单中的路径继续有效；正式路径映射在提升回执中核验。

A4读回正式文件逐字节核验，确认README全部入口可用、SHA一致、既有科学输入不变。输出`A07/promotion_receipt.json`、`formal_readback.json`及MAIN追加执行总结。

阶段A完成条件：正式O16存在、新G7与正式读回PASS、所有引用可解析。最终状态允许delivery_status=PASS，但scientific_status=REJECTED、recommendation_count=0、trusted=false。此时才能进入B00。

## 5. 阶段B固定数据规则

### 5.1 目标值与默认主/敏感性数据集

**相同结构，每个参数取全部器件的中位数，每个物理器件等权。**

- 主数据集：当前同批次、deembedded、geometry_eligible的器件记录，逐参数使用有限且满足既有有效性定义的测量值；保留文件名Fail的器件；保留FL0待复核59条及其flag，不因Qp高而删除。
- 敏感性数据集：在主数据集基础上仅对FL0审计59条屏蔽Qp，其他参数不自动屏蔽。其结果只用于评估筛选影响，不能择优替换主实验。
- 输入使用P3 `data/records.csv.gz`及原映射/来源证据；**不从structures.csv倒推器件数据，也不把P13模型预测当测量标签**。FL0 overlay只作标记来源，不能未经核验就直接使用它的全记录训练列。
- raw留作对照，不混入de标签；不新增批次/配方混合。历史配方未独立确认的局限继续保留，不伪造配置确认。
- 默认训练核心目标：k2eff_pct、qp、qs、bodeq_fitted。fs_ghz、fp_ghz及已有其他可用标量也按相同规则聚合输出，但本轮不扩展曲线网络训练。

### 5.2 结构与器件身份

结构键使用经来源确认的EG/FL/AG状态/面积/PF/拓扑/cohort及映射语义；相同几何允许合并不同mark，但不能合并未知拓扑。AG未知用明确unrecorded类别保留，不凭空填1。数值规范化使用现有映射精度，不用任意四舍五入合并邻近结构。批次/模式/处理口径单列，并禁止跨口径隐式合并。

**已发现旧physical_id移除了S11/S22前缀，不可直接作为本轮器件去重键。** 同一s2p的两个端口可能对应不同结构/面积。先核对映射与原始device_id：器件键至少区分批次、规范原始测量身份、端口及结构关联。存在重复时逐项列出，不能把不同端口当作同一器件删除。

完全重复导入同一条测量只计一次，保留别名；同一物理器件的真实重复测量先按参数取其重复值中位数，再进入器件总体中位数，标注重复数。若无法判明是重复还是独立器件，停止该归并并要求身份依据，不按数值接近猜测。

### 5.3 输出与缺失约定

器件长表：`device_key, structure_key, source_row_id, mode, parameter, value, valid, reason, audit_flag`。原记录数、唯一测量数、唯一器件数分别记录。

结构长表：`structure_key, parameter, median, n_devices_total, n_valid, n_missing, n_invalid, q25, q75, min, max, source_device_keys`。每参数分别统计；另一参数缺失不删除本参数有效测量。无有效值JSON null、CSV空字段，不填0。偶数样本中位数取中间两值均值。

有效0由各参数物理定义判断：不能将“非缺失”混同“物理有效”；EG/FL=0合法；Qp≤0按既有有效性契约处理并记录原因。中位数向量不是某个真实器件，也不保证恰好满足由非线性公式联系的所有标量；只做一致性诊断，不私改任一参数以强制匹配。

训练时每个结构每个目标权重相同，默认不按器件数量给高重复结构更大权重。某目标n_valid<5的结构标签照常输出，但对该目标标记低支持，在该目标主训练/主评分中不使用；其他目标不连带排除；单列预测/误差，不声称不合格测量。该样本数规则是本计划新实验的预注册默认值，不是旧P13规则。

## 6. 阶段B任务表与实现契约

| ID | 主责→验收 | 输入 | 产物与PASS条件 |
|---|---|---|---|
| B00 | A0→A4 | 正式O16、全部源表 | 独立B_RUN、输入哈希/协议，A交付不再写入 |
| B01 | A1→A4 | 器件源表、映射、FL0证据 | 身份与重复审计，无未解释跨端口合并 |
| B02 | A1→A4 | B01 | 中位数构建器、测试、主/敏感性标签与差异表 |
| B03 | A1+A2→A4 | B02 | 冻结分组划分、模型配置、门槛与预算，外层不可调参 |
| B04 | A1→A4 | B03 | 固定模型训练/内层选择；仅使用新标签 |
| B05 | A4+A2 | B04 | 外层回顾性验证、边界/低支持/敏感性报告 |
| B06 | A1→A4 | B05 | 如符合门槛，拟合研究模型并核验接口；否则明确不生成 |
| B07 | A0→A4 | 全部B证据 | 独立报告/清单/复现命令，主数据完整性验证 |

### B00–B02：先测聚合器，再生成标签

新增命名空间建议（执行前登记不存在，同名已存在则核对，不覆盖）：

- `backend/app/ml/device_median_v1.py`：纯数据聚合及身份验证，不改旧optimization_data.py行为。
- `backend/tests/ml/test_device_median_v1.py`：新规则测试。
- `backend/scripts/run_device_median_v1.py`：显式输入/输出CLI。
- `backend/scripts/verify_device_median_v1.py`：A4独立验证，不能只调用被测聚合函数生成期望值。

**以下是需实现的CLI契约，并非当前已有命令：**

```text
run_device_median_v1.py build --records <records.csv.gz> --identity <identity.json>
  --audit <record_audit.csv> --protocol <protocol.json> --out <B_RUN/data>
verify_device_median_v1.py --records <records.csv.gz> --identity <identity.json>
  --data <B_RUN/data> --out <B_RUN/B02/independent_check>
```

identity.json必须由B01生成且来源可追溯，不接受临时猜填。build拒绝覆盖非空输出，读取所有输入哈希并在结束时再核对。输出device_ledger、structure_targets_primary、structure_targets_sensitivity、aggregation_differences、exclusions、identity_conflicts、manifest。

B02最小测试集：

| 输入/场景 | 必须得到 |
|---|---|
| [1,2,100] | 2 |
| [1,2,3,100] | 2.5 |
| 坐标A=[1,2,3]，B=[100] | 2.5，禁止51 |
| 同一参数[1,null,3] | 2，n_valid=2、n_missing=1 |
| 某参数全缺失 | null；其他参数仍保留 |
| 合法0 | 保留，不经truthiness丢失 |
| 同测量重复导入 | 只计一次并记录别名 |
| 同物理器件多次实测 | 先该器件重复中位数，再器件总体中位数 |
| 同一s2p不同端口不同结构 | 不错误去重 |
| raw/de、不同拓扑或AG状态 | 不隐式混合 |
| 输入顺序打乱 | 标签、成员集合一致 |
| FL0待复核59条 | 主集保留并标记；敏感性只屏蔽Qp |
| 每参数不同缺失集合 | 逐参数中位数正确，不整行删除 |

A4对全部结构/全部参数用独立排序取中间元素复核，并与成员ID回溯；与旧按坐标聚合结果比较输出差异，不预设必须有多少结构变化。逐目标验证n_total=n_valid+n_missing+n_invalid（按去重后的器件口径）。PASS前不训练。

### B03：冻结实验，不追逐旧已看过测试结果

protocol.json至少包含schema版本、单位、数据版本/哈希、身份规则、主/敏感性总体、有效性规则、目标、n_valid门槛、划分、seed、模型网格、评分、失败处理、预算。由A2审查后A4签收；之后不可原地修改。

默认split：固定seed=20261102；普通留出按design_group_id做80%/20%分组划分，训练内部GroupKFold最多5折，至少3组；不足则该范围NOT_EVALUABLE。EG上边界、FL上边界、面积上下边界分别作为独立外层范围，界限由几何列表确定，不由性能选择；与留出结构同design_group的所有记录从该范围训练集中排除。每范围保存显式结构/器件ID，不仅保存随机种子。全局缩放、缺失处理和选择都在训练折内拟合。

主/敏感性使用同一组ID划分；若目标有效性导致样本变化，记录差异，比较时同时给共同样本结果。面积下边界的700-PF等已知难点单独报告，不从汇总中删除。所有历史数据的这轮留出都称“回顾性”，不是新盲测。

模型范围固定为：每折训练目标中位数常数基线；P3 ExtraTrees标量基线；P6C trend_gp_log_area标量基线。复用实现需核查输入语义，禁止加载旧目标模型权重冒充新训练。精确超参数从既有配置复制到新protocol并冻结；缺失时先定位配置，不在外层上补搜。M1/M2本轮不自动重训；M2的均值/其他估计目标不兼容问题只做审计，后续另版协议处理。

预算：每个模型/目标/外层范围只允许冻结配置一次完整运行；随机模型固定一个种子；运行到30分钟未完成时保存状态和资源记录，允许继续同一配置，不更换算法以追求结果；数据错误需新attempt。禁止因外层失败追加网格、种子或bootstrap。默认不生成预测区间，输出null及not_evaluated，不沿用旧标签的区间。

### B04–B06：训练、评估、研究模型

新增`run_device_median_models_v1.py`或等效新CLI，由A1实现并登记；旧P13训练入口和冻结数据不修改。接口统一：

```text
run_device_median_models_v1.py fit --protocol <B_RUN/protocol.json>
  --data <B_RUN/data> --splits <B_RUN/splits.json> --out <B_RUN/models>
run_device_median_models_v1.py evaluate --protocol ... --models ... --out <B_RUN/evaluation>
run_device_median_models_v1.py finalize --protocol ... --evaluation ... --out <B_RUN/research_bundle>
```

训练前必须跑新聚合/适配测试及完整ML回归，独立复核模型实际读入新标签哈希。每折输出训练ID、预测ID、预测值、真实中位数、误差、特征和目标变换。不同参数使用各自有效集合，不能因Qp缺失连带删掉k²标签。

A4验证主/敏感性各范围MAE、RMSE、偏差、n；k²单位是百分点，不是相对百分比；Qp单位无量纲。同时报告每结构误差、每分支误差、低支持层、旧/新标签差异以及样本集合变化。模型选择只用内层MAE；平局按预先固定顺序ExtraTrees优先，不参考外层结果。

**本计划新增的研究模型门槛（非P13历史科学门槛）：** 对k²和Qp，内层选定模型在每个可评估外层范围MAE均不高于同折常数基线；普通留出两目标MAE均至少降低5%。任一必需范围NOT_EVALUABLE或任一目标不通过，则overall=REJECTED，不产“合格研究模型”。Qs/BodeQ独立报告，不隐藏恶化。基线MAE为0时要求模型MAE也为0，该项不计算除零改善率。

符合上述门槛只允许生成全量新标签拟合的**研究包**，状态`RETROSPECTIVE_ONLY`且trusted=false；未通过则finalize只生成拒绝回执，不生成空壳PASS模型。不做候选搜索、不刷新推荐、不替换桌面HTML或生产服务。

研究包内必须有目标单位、训练结构范围、标签版本、缺失策略、分支拒绝规则、来源哈希及固定输入预测回读验证。数据集中未支持的结构返回unsupported；不能静默夹到边界后称外推。不同目标中位数组合不称为真实复阻抗曲线。

### B07：封存及交接

交付：`B_RUN/README.md`、`data/`、`protocol.json`、`splits.json`、`models/`或失败回执、`evaluation/`、`verification/`、`final_manifest.json`。只读验证P13与FL0原产物哈希未变，验证全部新产物可从输入重建。

完成报告须回答：全器件中位数是否落实；总记录/器件/结构数；重复与缺失如何处理；哪些旧标签改变；59条隔离对Qp中位数和结果影响；新模型是否达到新研究门槛；哪些范围不能评估；没有独立实测的限制。负结果亦完成B阶段，不能为得到模型PASS无限追加实验。

## 7. 阶段C：只准备独立实测协议

若未来要主张新结构k²提升1个百分点，需独立新器件/新测量。B07可附协议草案：冻结结构、模型版本、预测和评价规则；同批基准结构作对照；完整记录测量功率/IFBW/频率步长/校准件/去嵌配方；重复测量用于测量不确定度，不冒充更多独立器件；预先规定每结构器件数量与统计方法；目标仍为所有器件中位数。

没有制造/测量输入时只写NOT_RUN和所需资料。不编造样本量充分性，不将历史数据随机重新划分冒充独立测试，不因为软件交付PASS改变科学状态。

## 8. 全局停止表

| 触发 | 当前动作 | 恢复条件 |
|---|---|---|
| 未知文件/哈希变化 | 保留；停止依赖它的写入 | 所有者证据+A4签收 |
| O14/H04或科学输入发生意外变化 | 不更新期望哈希遮盖 | 查明归属；必要时另版协议 |
| null修复后数值或候选数改变 | 拒绝O15候选 | 修复生成逻辑并重跑A03–A05 |
| 测试少了、跳过了或仍读旧HTML | 拒绝T18 | 恢复覆盖并验证实际输入 |
| G7未通过 | 保留候选，不生成正式O16 | 新attempt独立G7通过 |
| 器件/端口身份不能确认 | 不错误去重、不训练 | 提供映射或测量身份依据 |
| 新旧训练目标不一致 | 不比较“模型改善” | 同一新标签、同一划分重新评估 |
| 主/敏感性结果不同 | 两者并列报告 | 不需要择优或改协议 |
| 科学门槛失败 | 输出拒绝和原因，完成报告 | 另版明确研究问题；不原地调门槛 |
| 无独立实测 | trusted=false | 完成冻结预测后的独立测量 |

## 9. 给接手代理的完整启动指令

> 按本计划执行，不先运行旧build/finalize。先读AGENTS/CLAUDE和P13执行总结，完成A00快照与A01归属独立签收。最大4个并发代理，实施者与A4验收分开。A02先隔离attempt及备份历史证据，再为当前构建/测试入口实现显式输出参数。A03用测试复现null缺陷，A04修生成器，A05重跑完整T18，A06独立G7，A07按字节提升正式O16。科学状态仍REJECTED、推荐0。之后另建B_RUN执行全部器件中位数数据/训练计划，主数据保留FL0待复核记录，敏感性单列；先身份核验再去重，每参数逐器件中位数，禁止坐标中位数二次聚合替代。固定模型与划分，回顾性验证失败也如实封存。不要重写P13冻结协议，不自动部署、推送、安装依赖、增加模型搜索或声称新实测外推通过。任一失败保留完整日志和哈希，从依赖图正确节点恢复。直到A和B分别形成可核验的正式结果或明确外部阻塞再停止。

## 10. 最终验收清单

- [ ] 归属独立签收，原GR0状态未篡改。
- [ ] 历史attempt完整保留，新工具不引用旧成功回执。
- [ ] null字段全部符合H04，0值未误伤，O14未变。
- [ ] 新T18、新独立G7、正式读回通过；O16可用。
- [ ] 中位数规则通过不均衡坐标、端口、重复和缺失测试。
- [ ] 全体器件主标签与清洗敏感性标签分别可溯源。
- [ ] 分组无泄漏，模型实际使用新标签，外层无调参。
- [ ] 研究门槛结果及失败范围完整；无独立实测仍trusted=false。
- [ ] 交付清单无循环哈希、无缺失路径；源数据和其他任务文件未被改写。

以上勾选项仅由执行证据填写，本计划编写不代表任何项已完成。
