# 平台优化执行日志

执行入口：`platform-optimization-luna-execution.md`。状态使用 pending/running/passed/blocked；未运行不写通过。

## 基线

- HEAD：`ab9bb94a6cc086920e1828c32ce2c75f96060263`
- B00采集时工作树：30个既有tracked差异、75个受保护文件；含本机同步/复制实现、部署资料、截图和打包文件。后续B02在两个既有脏文件中做了增量，其余73个文件摘要保持一致，见复查记录。
- 源码和所有非忽略工作文件摘要：`/var/folders/fr/k3vpq27d5sd5zsy0ll16r_ym0000gn/T/aln-platform-baseline-7dwr_sil/workspace.sha256`。摘要清单在仓库外临时目录，未复制数据文件。
- 前端在途差异已检查App、API端点、ServerSyncPanel和Electron main；详细保护范围见`platform-optimization-frontend-baseline.md`。

## 步骤记录

| 编号 | 状态 | 前置与文件 | 检查和实际结果 |
| --- | --- | --- | --- |
| B00 | passed | HEAD `ab9bb94a6cc086920e1828c32ce2c75f96060263`；30个既有tracked差异、75个既有受保护文件；新增 `backend/tests/fixtures/platform_cases.py` | 摘要复核75/75一致；Ruff通过；后端全测基线已区分为数据库环境失败与SQLite专用旧fixture错误；四种运行模式逐一标注检查状态，见下文 |
| F01 | passed | 在途前端差异清单新增至 `platform-optimization-frontend-baseline.md` | typecheck、Vitest、普通/静态构建、Plotly E2E均通过；测试产物仅在临时目录 |
| B01 | passed | `schemas/{source,processing,analysis,result_manifest,capabilities}.py`、`services/task_state.py`、前端 `platformContracts.ts` 与固定JSON样例、对应测试 | `pytest tests/services/test_task_state.py tests/api/test_contract_examples.py`: 31 passed；指定Ruff检查通过；临时前端副本 `npm run typecheck` 通过。状态服务使用SQLite隔离契约表验证CAS及lease；尚未迁移的真实processing_runs表留给B02/B04 |
| B02 | passed | B01/B02专项及新增COPY回归修复均通过；原模型/迁移链保持 | 2026-10-09恢复：COPY列补齐source_device_key；1行ORM与3000行真实临时PG COPY均逐条核对输入UUID，54 passed、1 warning、无skip；全量Ruff通过 |
| B03 | passed | B02 passed；修复旧mock并补齐两数据库专项验收 | 2026-10-09继续：扩展回归114 passed、1 warning、无skip；Ruff与diff检查通过，详见恢复记录 |
| B04 | passed | 独立task/run、租约领取与交接、投递失败例外、并发与实际worker回归 | 异常序列化修复后扩展86 passed，无skip；Ruff/diff通过。真实broker与native进程综合验收仍归B21 |
| B05 | passed | 默认commit=False、COPY savepoint、persisted路径和UUID目录、显示重命名 | 统一HTTP与lifespan隔离数据库后89 passed；补实际UUID解压最终91 passed，无skip；进入B06 |
| B06 | passed | 确定性预检查、ZIP成员安全验证、实际频段/端口与校准匹配、表格失败列表 | 首批32 passed；扩展59 passed，无skip；Ruff通过。异步job和draft API归B07 |
| B07 | passed | 异步预检、一次上传草稿、幂等消费、组恢复与旧入口共享 | 最终123 passed，无skip；SQLite/PG，队列类型与实际worker复用验证；最终真实执行器验收待B21 |
| B08 | passed | 幂等条目/问题、持久计数、分页与完整CSV、并行回退去重 | 最终108 passed，无skip；SQLite/PG与临时生成算法样本，不冒充缺失客户材料验收 |
| B09 | passed | staging、稳定ID、同事务发布、失败保留旧值/目录、延迟清理意图 | 功能门禁113 passed；另2个旧可选客户#2.zip集成用例因缺材料skip，明确未验证；两执行器最终验收归B21 |
| B10 | passed | 停止确认、租约写屏障、独立心跳及sync输入保留 | 最终129 passed，无skip；Ruff/diff通过，综合真实执行器待B21 |
| B11 | passed | 幂等重试、配置/算法/输入/来源校验、成功结果合并与历史保留 | 最终153 passed，无skip；Ruff/diff通过，前端G02尚未接入 |
| B12 | passed | 软删/恢复、有效数据作用域、保留输入与身份 | 真实SQLite/PG扩展108 passed，无skip；Ruff/diff通过 |
| B13 | passed | 文件租约、延迟清理、恢复巡检 | 真实SQLite/PG最终134 passed，无skip；Ruff/diff通过 |
| B14 | passed | B13 passed | 183服务/API回归及2真实native流程通过 |
| B15 | passed | B14 passed | 80项SQLite/PG及兼容查询回归通过 |
| B16 | passed | B15/B10 passed | 81项SQLite/PG、升级与分发门禁通过 |
| B17 | passed | B16 passed | 40项完整产物门禁、20项来源增量、72项回归及2真实native流程通过 |
| B18 | passed | B15/B17 passed | 96项SQLite/PG回归、2真实native流程通过 |
| B19 | passed | B14/B17 passed | 97项SQLite/PG门禁、2真实native流程通过 |
| B20 | passed | B01 passed | 17项模型API/元数据/静态发布回归通过 |
| F02 | passed | B01/F01 passed | 17来源/dataset/workspace测试及临时副本typecheck通过 |
| F03 | passed | F02 passed | 24项codec/来源/workspace回归及typecheck通过 |
| F04 | passed | F03 passed | 26项单元回归、5真实Chromium IDB流程、typecheck通过 |
| F05 | passed | F04 passed | V通过；144单测通过/1既有skip、9真实IDB/React流程通过 |
| F06 | passed | F05 passed | 149单测/11真实浏览器/三端JSON互导及V/S通过 |
| F07 | passed | F06/B15 passed | 157单测通过/1既有skip，typecheck及V/S通过 |
| F08 | passed | F07 passed，C05a已确认 | 34项SQLite/PG及164前端测试/typecheck通过 |
| F09 | passed | F08/B18 passed | 170前端/41后端/3真实DuckDB流程及V/S通过 |
| F10 | passed | F09 passed | 174单测/真实Plotly工作表联动及V/S通过 |
| F11 | passed | F10 passed | 177单测/typecheck及真实wafer复用回归通过 |
| F12 | passed | F11 passed | 180单测/真实PNG离线HTML打印及V/S通过 |
| F13 | passed | F12/B19 passed | 184单测/typecheck及V/S通过 |
| F14 | passed | F13 passed | 189单测/真实设置交互及V/S通过 |
| F15 | passed | F14 passed | 192单测/13真实项目设置流程及V/S通过 |
| G01 | passed | F15/B07/B08/B19 passed | 197单测/typecheck/真实native上传组及V/S通过 |
| G02 | passed | G01/B10/B11 passed | 203前端/37后端，typecheck/V/S及Ruff通过 |
| G03 | passed | G02/B12/B13/B14/F13/F14 passed | 208前端/36后端，typecheck/V/S及Ruff通过 |
| G04 | passed | G03/B16/B17/F06/F07/F13 passed | 213单测/真实native CSV+XLSX/2静态Worker/4双数据库及V/S通过 |
| F16 | passed | F15/G01–G04 passed；用户批准存储Worker | 215单测/1既有skip、typecheck/V/S、完整52 E2E及真实hover不保存专项通过；三端流程与P4实测见下文 |
| B21 | passed | B01–B20/F16 passed；用户确认结果表快照替代本次客户材料门禁，旧ZIP回归保留未验证 | 完整后端873 passed/19 skipped；最终45项真实回归、两数据库客户快照2 passed、浏览器报告2 passed、两数据库200001导出/百万发布2 passed，详见2026-10-12收尾 |
| F17 | passed | F16/B21 passed；新增最终验收交接文档，完成基线对照 | 43/43在用户确认范围内完成；原75资产均存在，12份前端基线副本SHA核验/增量patch归档，Ruff/diff通过、HEAD不变/stage空；原ZIP仍未验证，无提交/部署 |

## B00基线核查

- `git status --short`、`git rev-parse HEAD`、`git diff --name-only`、`git diff --stat` 已记录；tracked差异为30个文件（408 insertions、106 deletions），另有45个既有未跟踪文件。完整路径见本执行开始时的状态记录和摘要清单。
- 受保护摘要清单位于 `/var/folders/fr/k3vpq27d5sd5zsy0ll16r_ym0000gn/T/aln-platform-baseline-7dwr_sil/workspace.sha256`；独立SHA256复核75/75一致，无既有路径被本任务改写。
- `cd backend && uv run ruff check --no-cache .`：通过；新增夹具变更后复跑全量命令仍通过。新增夹具边界自检：有效DUT 2×S2P、1×S1P、2个校准文件、忽略文本、损坏Touchstone、重复ZIP成员、缺校准包、部分失败、同名不同来源、惰性200001行均符合要求；有效波形由现有处理器测试生成器生成；样本和ZIP均在系统临时目录。
- `uv run pytest`默认PostgreSQL连接拒绝；完整基线结果407 passed、16 skipped、11 failed、6 errors。使用不可达的临时端口65432及临时DATA_ROOT复核（`DATABASE_URL=postgresql+psycopg://aln:aln@127.0.0.1:65432/aln ALN_PROTECT_DB=1 uv run pytest --tb=no -q`）得到407 passed、19 skipped、11 failed、3 errors。失败涉及`tests/api/test_export.py`、`test_query_safety.py`和`test_reprocess.py`数据库连接；错误涉及`tests/workers/test_deembed_path.py`两例与`test_pipeline_batch.py::TestPipelineBatchTask::test_pipeline_batch_full_pipeline`。完整用例名见下方。
- 隔离SQLite基线（DB、DATA_ROOT、pytest cache和basetemp均在上述临时根目录）：418 passed、19 skipped、3 errors。3个错误是`test_deembed_path.py::test_deembed_with_calibration_writes_de_s1p`、`test_deembed_path.py::test_deembed_without_calibration_fails_loudly`和`test_pipeline_batch.py::TestPipelineBatchTask::test_pipeline_batch_full_pipeline`的既有fixture向SQLite发送PostgreSQL专用TRUNCATE语句；未改这些旧测试。
- 前端V/S/E复用F01结果：`npm run typecheck`通过；`npm test -- --reporter=dot` 130 passed、1 skipped；`npm run build`和`VITE_STATIC=1 npm run build`通过；`npm run test:e2e -- plotly.spec.ts` 6 passed。临时前端副本位于`/var/folders/fr/k3vpq27d5sd5zsy0ll16r_ym0000gn/T/aln-platform-baseline-7dwr_sil/frontend`。单测首轮仅因依赖软链接超出Vite临时文件allowlist失败，设置临时副本的allowlist后复跑通过；没有改动仓库Vite配置或依赖锁文件。

### 运行模式状态

| 运行组合 | 状态 | 证据/限制 |
| --- | --- | --- |
| 桌面SQLite | 需要检查 | 执行了隔离SQLite测试，但未启动真实桌面进程；不能据此宣称桌面验收通过 |
| 原生SQLite服务器 | 需要检查 | 本步骤没有启动原生服务器执行器 |
| PostgreSQL + Celery | 需要检查 | B00默认PostgreSQL不可连接；B02及复查在临时PG数据库的专用schema验证迁移通过。Redis/Celery实际执行器流程未验收 |
| 静态版 | 已检查 | `VITE_STATIC=1 npm run build`通过；仅构建验收，不等同浏览器静态端到端验收 |

PostgreSQL基线失败用例：

```text
tests/api/test_export.py::test_export_csv_returns_header_only_when_no_data
tests/api/test_export.py::test_export_csv_filename_is_quoted
tests/api/test_query_safety.py::test_filter_tree_within_depth_succeeds
tests/api/test_query_safety.py::test_filter_empty_group_accepted
tests/api/test_query_safety.py::test_query_devices_works_with_only_batch_no_field
tests/api/test_query_safety.py::test_query_aggregate_works_with_only_batch_no_group_by
tests/api/test_query_safety.py::test_query_devices_order_by_batch_no
tests/api/test_query_safety.py::test_query_devices_order_by_batch_no_desc
tests/api/test_query_safety.py::test_query_aggregate_with_batch_no_in_filter_tree
tests/api/test_query_safety.py::test_query_distinct_batch_no_works
tests/api/test_reprocess.py::test_recompute_rejects_missing_batch
```

## B01结果

- 新增后端契约文件：`backend/app/schemas/source.py`、`processing.py`、`analysis.py`、`result_manifest.py`、`capabilities.py`。字段和状态字面值按C01/C02/C05/C09；SourceRef UUID与未知修订显式为空，AnalysisSpec校验source/workspace比较引用形状，ResultManifest校验total_relation与matched_total的一致关系，Capabilities校验mode/database组合。
- 新增 `backend/app/services/task_state.py`，唯一转换表覆盖成功、失败、取消请求和取消终态；同状态终态返回noop。更新使用任务status CAS、run_id/task_id/status/lease_token条件更新及savepoint，冲突不会只改其中一端。
- 新增 `frontend/src/types/platformContracts.ts` 与 `platformContracts.example.json`；后端API测试直接读取同一JSON样例，避免前后端各维护一份样例。
- 新增 `backend/tests/services/test_task_state.py`、`backend/tests/api/test_contract_examples.py`。固定JSON、空身份、未知状态/阶段/类型、schema版本、total关系、能力数据库组合和每种允许转换均已覆盖。
- 检查：`uv run pytest tests/services/test_task_state.py tests/api/test_contract_examples.py -q`：31 passed；计划要求的Ruff命令通过；临时前端副本`npm run typecheck`通过。
- 本步骤没有引入processing_runs ORM或迁移。状态服务按C02表字段执行条件更新，测试使用临时SQLite契约表；B02完成真实表后、B04接入worker前仍须验证运行时集成。

## B02结果

- 新增模型及Alembic表覆盖 `processing_runs`、`processing_items`、`quality_issues`、`staged_device_rows`、`source_inputs`、`upload_drafts`、`export_jobs`、`sync_operations`、`local_replica_preferences`、`tombstones`、`cleanup_jobs`；Batch/Device/UploadTask 扩展字段与约束同步加入。`sync_revisions`、`replica_batches`、`sync_identity`、`table_receipts` 是B00已存在的受保护在途模型，SQLite create_all注册它们；本次PG Alembic未创建这四表，PG元数据扩展属于B03，不能算B02新增交付。
- 新增Alembic `4b38220ae4eb`，唯一父节点为既有 `2026_09_15_120000`；`alembic heads` 只有该单一head。SQLite v4→v5共享升级路径扩展为新表创建和任务状态约束升级。
- `uv run pytest tests/models/test_platform_schema_upgrade.py -q`：SQLite迁移用例通过；加 `ALN_TEST_POSTGRES_URL='postgresql+psycopg://aln_test@127.0.0.1:55432/aln_test'` 后 5 passed、1 warning。PG只在临时数据库的唯一临时schema中升级并在finally删除；结束后确认无 `aln_migration_test_%` schema遗留。测试覆盖新库、v4数据/索引/外键保留、重复幂等、失败回滚、未来schema版本拒绝。
- `uv run pytest tests/test_desktop_mode.py -q`：9 passed；`uv run ruff check --no-cache .` 与 `git diff --check` 通过。
- PostgreSQL首次验证仅在Alembic SQL执行前因ConfigParser将百分号当插值语法失败；`backend/alembic/env.py` 现对URL百分号做ConfigParser安全转义，临时PG schema复测通过。此次新增URL处理覆盖百分号编码连接参数。
- 既有工作树只在 `backend/app/desktop_setup.py` 和 `backend/app/models/__init__.py` 两个脏文件中有本任务增量；既有改动均保留，其余73个受保护文件摘要未变。

## B03进展

- 已读取C01和B03步骤；目前确认 `replica_service.init_replication` 仅初始化SQLite的 `sync_identity` / `sync_revisions` 和无条件UPDATE修订触发器，PostgreSQL直接返回；查询/聚合缓存key目前只含请求摘要，distinct key只含字段与limit。B03必须同时覆盖来源修订与全局查询epoch，不能用Batch.result_revision替代。
- 实施前核对文件树后发现阻塞，不继续修改其他B03职责，也未运行B03测试。

### B03阻塞证据

- 契约C01与B03要求：文件树移动/重命名后同步 `source_inputs.current_relpath`，同时保持 `member_key` 与器件UUID不变。
- 现有 `backend/app/models/file_node.py` 的模型说明明确写着文件树的重命名、移动、排序“均只改本表，不改磁盘文件”，以保持 `devices.s_param_path` 稳定。
- 现有 `backend/app/api/files.py::move_file_tree_nodes` 只改 `FileNode.parent_id` 后提交；`rename_file_tree_node` 只改 `FileNode.name` 后提交。两者都没有移动磁盘文件、修改 `FileNode.relpath` 或修改 `Device.s_param_path`。
- 因此将虚拟树中的目标位置写入 `current_relpath` 会令路径映射与实际文件位置不一致；保留真实相对路径则无法按B03字面要求在这些虚拟操作后更新映射。擅自把虚拟操作改成物理移动会改变既有文件树语义，超出本步骤已定方案。
- 需要先明确该字段应跟随实际磁盘路径（虚拟树操作不更新）还是跟随虚拟树展示位置（需同步调整路径解析与重处理行为）。澄清前B03整体保持blocked；来源身份、修订触发器、缓存epoch及快照/COPY工作均未开始，不能记为通过。

## 2026-10-09 Git与门禁复查

- 用户本轮要求先检查Git，再提出解决方案；本轮仅新增审阅文档并校正执行记录，未实施业务修复。详细结果与建议见 `platform-optimization-git-audit.md`。
- HEAD仍为 `ab9bb94a6cc086920e1828c32ce2c75f96060263`，暂存区为空。受保护摘要复查75项：73项一致、两项有已记录B02增量、0项丢失。新增审阅文档前tracked diff为34文件、445 insertions/111 deletions；不能将这些包含用户在途改动的整文件差异作为本任务提交。
- `uv run alembic heads`：唯一head `4b38220ae4eb`；`uv run ruff check --no-cache .` 和 `git diff --check`通过。
- 在 `/tmp/aln-platform-git-audit-*` 临时DATA_ROOT、SQLite URL、pytest cache/basetemp下复测B01、B02、desktop与COPY回归，PG仍使用临时数据库内独立创建并清理的schema：`uv run pytest tests/services/test_task_state.py tests/api/test_contract_examples.py tests/models/test_platform_schema_upgrade.py tests/test_desktop_mode.py tests/workers/test_copy_insert.py -q`：51 passed、1 failed、1 warning，无skip。
- 新失败 `tests/workers/test_copy_insert.py::test_copy_columns_match_device_table`：Device表列比COPY列表多 `source_device_key`。B03计划虽已列出补齐COPY字段，但B02新增模型字段已使既有回归失败，B02门禁重新标blocked，先修复并复测才能继续。原迁移专项结果保留，不将这个失败归为旧基线或环境缺失。
- 文件树解决方案建议：current_relpath跟随实际解析文件位置，现有虚拟移动/重命名/排序保留物理相对路径；真实结果路径替换在发布事务中更新相关映射。此处为审阅建议，尚未修改C01/B03规范或业务代码。

## 2026-10-09恢复执行

- 用户授权采用前述解决方案并继续修改代码。C01/B03已同步区分实际解析路径与虚拟文件树展示位置，B09保留发布路径切换的事务验收。原B03措辞阻塞已解除。
- 修复 `device_ingest.DEVICE_COPY_COLUMNS` 漏项；新增 `tests/workers/test_copy_identity.py`，在真实临时PG schema分别通过1行ORM及3000行COPY核对所有输入UUID；schema finally清理，无生产/用户数据库写入。
- 复跑B01/B02/desktop/COPY及真实PG身份检查：54 passed、1 warning、无skip；全量Ruff通过，B02恢复passed。B03开始实施，未提前声明处理发布或用户界面流程完成。

## B03实现与新增失败停止（2026-10-09）

- 前置：B02已通过；本轮用户授权修改代码并继续执行。C01/B03实际路径与虚拟树语义已统一；没有改变原虚拟文件树架构。
- 改动文件：新增 `backend/app/models/data_epoch.py`、`services/source_identity.py`、`services/data_revision.py`、`backend/alembic/versions/bd03a1e9f052_data_revision.py`、`backend/tests/fixtures/platform_db.py`、`tests/services/test_source_identity.py`、`test_data_revision.py`。增量更新 `models/__init__.py`、`services/replica_service.py`、`snapshot_service.py`、`upload_service.py`、`device_ingest.py`、`compute_service.py`、`batch_stats_service.py`、`api/query.py`、`api/devices.py`、`main.py`、`scripts/export_web_snapshot.py`、`tests/services/test_batch_stats_service.py`。
- 实现：源输入按原始POSIX成员路径与端口精确登记，拒绝摘要冲突与不安全路径；旧本地记录分块回填，旧远端记录不生成本地来源身份。实际路径映射可在调用方发布事务中更新，未伪造旧记录原始ZIP成员。PG/SQLite业务字段触发器推进事务epoch与同步修订，空更新、操作字段不推进。PG迁移冻结触发器SQL；启动回填在本地worker开始前完成。
- 实现：查询/聚合/distinct缓存包含origin+epoch；曲线缓存包含文件stat与数据库epoch。物化统计读取检查stats_epoch，不新鲜则实时回退，刷新失败回滚会话。快照Arrow保留Device源UUID，批次/manifest携带源引用；远端导入保留输入UUID，旧数据缺身份明确unverified。静态导出只读打开源库并显式BEGIN，不写回源库；所有导出测试产物位于临时目录。
- 指定B03检查：`uv run pytest tests/services/test_source_identity.py tests/services/test_data_revision.py tests/api/test_query_cache.py tests/api/test_devices_cache.py -q`，临时DATA_ROOT/SQLite URL、临时cache/basetemp，显式 `ALN_TEST_POSTGRES_URL=postgresql+psycopg://aln_test@127.0.0.1:55432/aln_test`：27 passed。新增真实SQLite与PG测试覆盖同名异目录、大小写、双端口、重复登记、旧记录回填、原始键与UUID保留、实际映射更新、NULL业务值变化、bulk更新、空更新、操作字段、回滚、初始化幂等；Redis传输用fake，数据写入/查询用真实数据库，尚未验证真实Redis进程缓存路径。
- 扩展回归命令：`uv run pytest tests/services/test_source_identity.py tests/services/test_data_revision.py tests/api/test_query_cache.py tests/api/test_devices_cache.py tests/services/test_snapshot_service.py tests/services/test_replication_v2.py tests/services/test_batch_stats_service.py tests/services/test_upload_service.py tests/services/test_compute_service.py tests/web/test_web_snapshot.py tests/models/test_platform_schema_upgrade.py tests/test_desktop_mode.py tests/workers/test_copy_insert.py tests/workers/test_copy_identity.py -q`，同样使用独立临时DATA_ROOT/cache/basetemp。实际结果：97 passed、3 failed、1 warning、无skip；完整输出 `/tmp/aln-b03-regression-6MLyuq/results.log`。PG head迁移与真实1/3000行ORM/COPY回归包含在通过项中，不代表Celery执行器流程验收。
- 新增失败：`test_compute_service.py::test_upsert_device_insert`、`test_upsert_device_update`、`test_compute_single_device_happy_path`。调用 `_upsert_device` 新增的 `identity_for_result_path` 读取 `db.scalars(...).all()`；三个旧测试仅配置 `db.scalar`，未配置 `db.scalars`。MagicMock列表的长度/索引组合返回MagicMock，随后 `validate_uuid` 抛 `SOURCE_IDENTITY_INVALID`。失败证据定位 `services/compute_service.py:60` / `services/source_identity.py:31`；这是真实新增测试失败，不按基线或环境问题忽略。
- 解决方案（未执行）：为旧mock测试明确返回空SourceInput结果，保持原插入/更新断言；补充真实SQLite/PG计算入库测试，证明登记UUID继承、无登记时不冒认来源、既有UUID不被空值覆盖及歧义拒绝。不得通过绕过UUID校验、特殊识别MagicMock或把空登记伪造为UUID修复。修复后重跑本次完整命令，再补齐下面剩余验收，全部通过才把B03改passed并进入B04。
- 尚未完成B03专项验收：调用真实虚拟move/rename路由并核对磁盘可读及登记不变；staging/心跳不推进epoch的专项断言；登记器件重新计算的真实两数据库路径；新版来源身份快照跨端往返及静态旧/新manifest的专项核对；真实PG COPY触发epoch的明确断言；曲线同stat但epoch变化的缓存失效断言；真实PG物化视图陈旧回退/刷新新鲜度测试。不得把通用回归通过等同这些新增要求已完成。B09仍需原子发布路径切换与取消/失败保留旧路径测试。
- 最后只读Git保护复查：HEAD仍为 `ab9bb94a6cc086920e1828c32ce2c75f96060263`，stage为空，75个基线文件中67个摘要一致、8个有职责明确增量、0个丢失。变动的受保护文件为devices.py、desktop_setup.py、main.py、models/__init__.py、replica_service.py、upload_service.py及C01/B03分册；前端原在途文件保持基线摘要。
- `uv run ruff check --no-cache .`、`git diff --check`最后均通过。开发中长行/导入整理已完成；最终失败仍为上述三项。未提交、push、部署、发布或迁移用户数据库。
- 下一编号仍为B03（blocked修复与剩余验收）；B04与后续未开始。三端前端与两执行器最终验收尚未执行，整体任务未完成。

## B03恢复与通过

- 用户“继续”授权修复上次新失败并继续原步骤。恢复前Git状态、二进制diff和工作文件SHA256保存于临时目录 `/var/folders/fr/k3vpq27d5sd5zsy0ll16r_ym0000gn/T/aln-b03-resume-_evgi68j`；不stash/reset/整文件提交。
- 旧计算服务的三个mock测试现在明确配置 `db.scalars(...).all()`为空登记；没有绕过生产UUID校验。
- 新增SQLite/PG真实计算提取与upsert测试（使用生成共振文件，mapping加载用空表替代）：登记UUID与器件整数ID重算保留，无登记不按basename冒认，旧UUID不被空值覆盖，多个原始成员映射同活动路径拒绝。真实move/rename路由保持原磁盘文件可读、原始成员/UUID/实际路径不变，不推进epoch。
- 新增SQLite/PG快照往返来源UUID/修订保留；run创建/heartbeat/staging不推进epoch或自毁base版本；Mapping/Process真实业务更新推进epoch；同文件时间/大小条件下曲线与BodeQ缓存因epoch失效（Touchstone解析真实，BodeQ算法使用轻量marker替代，算法数值准确性不由此测试证明）。
- 真实PG ORM与3000行COPY明确核对epoch增加写入行数；真实PG陈旧物化视图回退devices、刷新后stats_epoch恢复新鲜度。静态新快照manifest与Arrow UUID核对、旧库快照unverified，导出前后源库SHA256不变。
- 检查命令为上轮完整扩展回归清单，补入同文件新增测试，环境仍使用临时DATA_ROOT/SQLite URL/cache/basetemp及显式临时PG独立schema：114 passed、1 warning、无skip；`uv run ruff check --no-cache .`通过。所有PG schema在finally清理。真实Redis/Celery、三端完整最终用户流程仍未验收。
- B03通过后进入B04。B09仍负责staging计算、原子发布与失败/取消时旧结果保护，不能据B03服务级通过宣称这些流程已交付。

## B04实施前契约冲突停止

- B03最终回归完整输出：`/tmp/aln-b03-resume-check-kHeObz/results.log`，114 passed、1 warning、无skip。三个旧mock失败已全部修复。
- 冲突来源：C02（`platform-optimization-luna-contracts.md:45`）固定 `pending → running → success|failed`；B04要求“投递发生在持久pending之后；投递失败保留输入并标failed”。队列拒绝投递时worker未领取租约，不存在真实running阶段。
- 当前实现证据：`backend/app/services/task_state.py` 唯一转换表pending只允许running/cancel_requested；只读调用 `can_transition('pending','failed')` 实际False，`can_transition('pending','running')`为True。现有upload_service在投递异常直接写task.failed，属于B04需消除的旁路；不能沿用以绕过契约，也不能只改task使run留pending。
- 推荐解决方案（未执行）：在C02显式允许“尚未领取、dispatch_phase=queued、租约/状态CAS匹配、已确认投递失败”的pending→failed，并以结构化DISPATCH_FAILED问题记录结束同一task/run、释放active_run_id、保留输入。扩展状态服务时限制该失败原因与queued阶段；普通worker进度及已领取任务不获得pending失败旁路。投递结果不确定（可能已入队）须另行明确，不按确认失败放宽租约竞争。
- 备选方案（未执行）：坚持当前C02图，队列失败保持pending，持久记录投递错误/可重投状态，返回503；相应修订B04“标failed”的要求。两方案都会影响用户可见状态和后续恢复行为，不擅自择一。
- 遵守用户停止规则：B04保持blocked，未新建processing_run服务、未改worker/dispatch，B05及后续pending。需要明确上述契约规则后才能执行B04动作与测试。三端与两执行器最终验收未执行，整体未完成。

## B04契约澄清后实施与门禁停止

- 用户明确选择“允许确认投递失败的queued任务转failed”；C02/B04已经同步该例外，投递结果不确定不能视为确认失败。原冲突解决；以下是后续实际执行结果。
- 状态服务 `transition_task` 增加仅pending→failed的确认投递失败入口，检查queued阶段并用phase/status/token CAS同步task/run；一般状态图仍不允许pending→failed。新增 `services/processing_run.py` 以fresh Session运行，SQLite BEGIN IMMEDIATE / PG FOR UPDATE锁批次，提供create/claim/heartbeat/assert/finish/handoff及确认/不确定投递失败记录。每次创建独立task/run；先回填源身份再捕获base修订；领取与阶段交接换token，拒绝重复领取/旧token；结束清active_run_id并保留旧task。
- 上传与重处理接口增量接入run创建、错误处理和run_id响应；dispatch、LocalTask和本地执行调用传递run_id/lease_token。新增 `workers/run_execution.py` 作为Celery及本地子进程共用阶段领取入口，独立线程通过另一Session心跳，extract→compute交接token。redeembed的有run分支先返回计算输入，分发链/本地调用再进入compute；无run历史调用维持兼容。
- ProgressPublisher增量用租约条件限制新run进度/终态，普通task UPDATE携带running/lease/active_run谓词；新run事件包含schema_version/run_id/event_seq/status/stage/progress/summary，event_seq持久在run摘要。无租约旧publisher不覆盖新run。cancel的最终确认仍属于B10，未把请求停止当成已停止。C02心跳/过期/取消/staging参数进入Settings。
- 文件清单：新增 `backend/app/services/processing_run.py`、`backend/app/workers/run_execution.py`、`backend/tests/services/test_processing_run.py`、`backend/tests/workers/test_run_dispatch.py`；增量 `services/task_state.py`、`upload_service.py`、`device_ingest.py`、`api/batches.py`、`schemas/batch.py`、`config.py`、`workers/{dispatch,local_queue,local_worker,extract_batch,compute_batch,reprocess_batch,progress,cancel}.py`及C02/B04分册。已有用户脏文件只围绕该职责做增量，不整文件提交。
- 首批服务检查：`uv run pytest tests/services/test_processing_run.py tests/services/test_task_state.py -q`，临时DATA_ROOT/SQLite URL/cache/basetemp，真实临时PG独立schema：26 passed。
- 完整B04门禁及受影响回归：`uv run pytest tests/services/test_processing_run.py tests/workers/test_run_dispatch.py tests/api/test_reprocess.py tests/services/test_task_state.py tests/services/test_upload_service.py tests/workers/test_reprocess_batch.py tests/workers/test_extract_progress.py tests/workers/test_deembed_progress.py tests/workers/test_cancel.py tests/test_desktop_mode.py -q`，同样临时环境并显式 `ALN_TEST_POSTGRES_URL=postgresql+psycopg://aln_test@127.0.0.1:55432/aln_test`：67 passed、1 failed、无skip。完整证据：`/tmp/aln-b04-gate-QCBpU2/results.log`。
- 失败：`tests/api/test_reprocess.py::test_recompute_rejects_missing_batch`。该旧文件在模块级建立TestClient，没有初始化测试库或override get_db；本次隔离DATABASE_URL指向空的临时SQLite。HTTP请求在 `_start_reprocess` 首条SELECT即报 `sqlite3.OperationalError: no such table: batches`，未进入新增run服务。B00曾记录数据库环境类失败，但此指定门禁仍不能算通过，也不能假定改回用户数据库后就通过。
- 解决方案（尚未执行）：将该HTTP测试改成fixture，使用已建表的临时SQLite/PG Session、对app.get_db设置依赖覆盖并finally恢复；保留HTTP 404断言，增加HTTP独立任务/409/投递失败路径。不得捕获缺表异常返回假404、跳过测试、使用用户现有库作测试数据或在生产API自动建表掩盖环境问题。
- 尚缺B04验收：真正双连接并发提交/锁等待竞争、实际worker阶段而非装饰器轻量核心的租约检查、长计算独立心跳、迟到进度与终态竞争、恢复后活动run处理。当前transport测试拦截Celery chain并核对参数，不能当真实broker/Celery或native子进程完整流程验收。旧worker的活动Device写入与文件替换将在B05/B09按顺序重构，当前不得宣称原子发布已实现。
- 遵守停止规则：B04保持blocked，B05及后续pending；最后Ruff与git diff检查通过，HEAD和stage未改变。未提交/push/部署/发布/迁移用户数据库；整体三端与两执行器验收未完成。


## B04继续：隔离HTTP与实际worker门禁

- 前置B00/F01/B01/B02/B03已完成。沿用保护快照 `aln-b04-resume-z0fxk_m7`；HEAD仍为 `ab9bb94a6cc086920e1828c32ce2c75f96060263`，stage为空。原75项保护资产复核：{'unchanged': 65, 'changed': 10, 'missing': 0}，无删除。所有测试数据库、样本、日志/cache均在临时目录。
- 增量文件：`tests/api/test_reprocess.py` 使用实际建表的SQLite/PG与get_db覆盖；`tests/services/test_processing_run.py` 补Barrier双连接提交/领取、旧阶段token与cancel_requested迟到更新；`tests/workers/test_run_dispatch.py` 补独立Session心跳、实际recompute worker执行有效S1P与重复投递、启动恢复保护。`app/workers/local_worker.py` 的旧恢复函数排除ProcessingRun关联任务，避免只写task.failed导致task/run分裂；完整租约过期巡检仍归B13，当前不会擅自推断queued投递失败或重放任务。`run_execution.py`仅澄清历史入口注释。
- 扩展命令：`uv run pytest tests/services/test_processing_run.py tests/workers/test_run_dispatch.py tests/api/test_reprocess.py tests/services/test_task_state.py tests/services/test_upload_service.py tests/workers/test_reprocess_batch.py tests/workers/test_extract_progress.py tests/workers/test_deembed_progress.py tests/workers/test_cancel.py tests/test_desktop_mode.py -q`，指定临时SQLite DATABASE_URL/DATA_ROOT/cache/basetemp与临时PG URL。首轮79 passed，无skip，证据 `/tmp/aln-b04-resume-gate-2zCAoH/results.log`。加入实际worker与恢复检查后83 passed、2 failed，无skip，证据 `/tmp/aln-b04-worker-gate-WD49kL/results.log`。
- 两个新增失败为 `test_actual_recompute_worker_retains_lease_and_rejects_duplicate[sqlite/postgresql]`。有效S1P第一次执行、实际qs入库、task/run同步success均已通过；第二次同token投递在claim被拒绝，但Celery `.apply().get(propagate=True)` 返回RuntimeError而非RunConflictError。不能将失败断言改宽后声称已保留结构化错误。
- 根因只读验证：RunConflictError构造需要code/message两参数，但Exception.args仅含message；pickle往返报 `TypeError: RunConflictError.__init__() missing 1 required positional argument: 'message'`；Celery `get_pickleable_exception`降级RuntimeError，code丢失。
- 解决方案（本轮停止后未执行）：为RunConflictError定义稳定的序列化重建方式（保留code/message和用户可读字符串），补pickle/Celery往返检查，再重跑上述门禁；检查通过后才标B04 passed并进入B05。真实Celery broker/Redis和native子进程综合验收仍留B21，当前实际worker.apply不是进程级验收；B09原子发布尚未实现。
- `uv run ruff check --no-cache .`及`git diff --check`通过。遵守新增失败停止条件，B04保持blocked，下一编号仍B04；无提交/push/部署/发布。三端前端和两执行器最终验收未完成，整体任务未完成。


## B04异常传输修复通过，进入B05

- 新保护快照：`/var/folders/fr/k3vpq27d5sd5zsy0ll16r_ym0000gn/T/aln-b04-pickle-resume-j3kpa9ku`，包含status、全部工作树diff和脏文件hash。未stage/commit。
- `RunConflictError.__reduce__`明确按code/message重建，保留原可读字符串；新增pickle与Celery异常传输往返断言。实际recompute worker两数据库的重复投递现在保留RunConflictError，租约拒绝行为不变。
- 重跑上轮相同扩展门禁，临时SQLite/DATA_ROOT/PG独立schema/cache/basetemp：86 passed，无skip。证据 `/tmp/aln-b04-pickle-gate-KGPH5j/results.log`。`uv run ruff check --no-cache .`、`git diff --check`通过。
- B04标passed，下一编号B05。实际worker.apply校验不代表真实broker/Celery/native子进程最终流程；B09原子发布与B13完整恢复巡检仍未实现。三端与两执行器总体门禁未完成。


## B05事务与路径边界实施，新增门禁错误停止

- 前置B04已通过。文件漂移：计划所指`rename_batch_service.py`实际为同职责`batch_rename_service.py`，已定位后按原职责修改，无架构替换。
- 改动：`device_ingest.py`的COPY/ORM入口默认commit=False，COPY尝试用begin_nested savepoint包围，异常回滚后ORM回退；`compute_service.py`的单文件upsert与数量更新默认不提交，flush后由调用者明确提交；compute/pipeline worker原分块调用显式commit=True，单文件API设备与数量同事务提交。
- `file_tree_service.py`接受预扫描manifest和commit=False，不传manifest的兼容调用先扫描再写树；出版传manifest时不访问磁盘。调用方显式提交。`batch_files_dir(Batch)`使用实际file_path，缺失路径才兼容原命名目录。`api/files.py`所有列表/曲线/下载/拆分/虚拟树调用改为传Batch；raw_download仅接收已解析路径，无批次号拼接。extract/pipeline新目录使用UUID，已有旧目录可按持久路径读取。旧活动Device删除/文件发布策略仍须B09替换，当前不声称原子发布完成。
- `batch_rename_service.py`只改批次显示名、关联任务展示名和虚拟根名，保留file_path/raw_zip_path；新增active_run与cancel_requested门禁。不再磁盘rename或按字符串替换ZIP路径。旧rename成功测试改为核验持久旧路径，而非期望移动目录。
- 新增`test_ingest_transactions.py`：真实SQLite/PG设备、数量和manifest树整体rollback、显式提交，实际PG COPY成功后制造数据库除零错误中止savepoint，再验证回退无重复、外层工作保留。新增`test_managed_batch_paths.py`：UUID和旧命名目录、ZIP及其他run目录保留，HTTP列表/下载可读。`test_file_tree.py`改用隔离真实SQLite/PG与临时已解压样本，保留树移动/排序/删除/下载断言；不将此项当上传执行器验收。旧compute/COPY单测按新默认提交契约更新；已有真实COPY身份测试明确提交。
- 门禁：`uv run pytest tests/services/test_ingest_transactions.py tests/services/test_managed_batch_paths.py tests/api/test_file_tree.py tests/api/test_rename_batch.py tests/services/test_compute_service.py tests/workers/test_copy_insert.py tests/workers/test_copy_identity.py tests/services/test_source_identity.py tests/services/test_processing_run.py tests/workers/test_run_dispatch.py -q`，独立临时DATA_ROOT/SQLite URL/cache/basetemp和专用PG schema。实际84 passed、5 errors，无skip。证据 `/tmp/aln-b05-gate-2oRHN7/results.log`。
- 错误均在旧`test_rename_batch.py`五例的client fixture初始化。API conftest建表于请求override的内存SQLite，但`with TestClient(app)`触发lifespan使用另一个app.main.engine（临时空SQLite）；`init_replication→seed_revision_metadata`查询batches失败。测试尚未执行rename端点，不能宣称旧API回归通过。
- 解决方案（停止后未执行）：将rename测试fixture的启动engine/Session与请求get_db覆盖统一为同一隔离已建表数据库，或将纯HTTP测试明确为无lifespan客户端并独立保留真实启动验收；不得通过忽略生产初始化错误或改用户DATABASE_URL绕过。然后重跑同门禁；通过后继续补查UUID worker目录、兼容调用与剩余回归，全部通过才进入B06。
- `uv run ruff check --no-cache .`及`git diff --check`通过。保护资产复核：{'unchanged': 65, 'changed': 10, 'missing': 0}，无缺失。HEAD不变，stage为空；无提交/push/部署/发布。B05保持blocked，B06及后续pending，三端与两执行器综合验收未完成。


## B05继续：隔离启动数据库与实际UUID解压通过

- 新保护快照 `/var/folders/fr/k3vpq27d5sd5zsy0ll16r_ym0000gn/T/aln-b05-resume-632em72w`。不改业务初始化：仅修复`tests/api/conftest.py`，将app.db.engine/SessionLocal和app.main.get_settings指向同一fixture引擎与临时配置，保留真实lifespan，并保存恢复原dependency_overrides。
- 重跑上轮相同门禁89 passed，无skip，证据 `/tmp/aln-b05-resume-gate-FaGgZs/results.log`。补`test_managed_batch_paths.py`真实SQLite/PG extract worker用有效S1P解压，核验UUID4目录、父目录配置、文件落盘、旧结果与rawZIP保留、handoff状态。最终相同门禁91 passed，无skip，证据 `/tmp/aln-b05-final-gate-DfucLf/results.log`。Ruff通过。
- 已搜索全部服务调用处：历史compute/pipeline分块明确提交，单文件API在设备和数量写入后一起提交；tree调用明确提交，B09可传manifest使用无提交路径。用户文件未删除，stage/HEAD未改。B05标passed，下一编号B06。进程级两执行器与三端最终验收未完成。


## B06预检查服务通过，进入B07

- 前置B05已通过；符号漂移已核实：计划`_rows_from_xlsx`实际公共入口为`app.share_pack.rows_from_xlsx`，保留其表头契约并记录全部failures，不重写解析器。
- 新增`services/upload_precheck.py`：validate_upload_options/inspect_archive/inspect_table/build_precheck_report/calculate_check_digest；原始input SHA256、规范化options、mapping文件SHA256与目标revision共同构成稳定check_digest，检查期间输入/mapping变化fatal PRECHECK_STALE。读取实际Touchstone频率/端口/有限采样而不计算谐振指标；未命中mapping、缺校准、频段非法/不匹配、空包、必需表列缺失fatal；坏Touchstone/无关文件/表格失败行warning保留。单文件S1P/S2P/SNP用同一检查器；S1P和表格去嵌来源unknown，勾选不会令deembedded=true。
- 新增`archive_validation.py`共享ZIP成员检查：上传及解压容量使用已有UPLOAD_MAX_GB，拒绝路径逃逸、symlink、重复/大小写碰撞与文件目录冲突；legacy `_extract_zip`和StreamingExtractor都在任何实际解压前调用。`core/deembed.py`暴露既有方法分类器包装，预检查用CalibrationIndex及match校准，不写新前缀猜测。
- tests/services/test_upload_precheck.py用临时真实样本覆盖上述问题、双端口、确定性摘要与mapping/target变化、表格partial/列缺失、legacy/streaming解压入口拒绝非法路径、SQLite/PG无Device写入，算法入口替换为失败哨兵确保不计算指标。
- 首批 `uv run pytest tests/services/test_upload_precheck.py tests/workers/test_extract_progress.py tests/services/test_managed_batch_paths.py -q`：32 passed。补StreamingExtractor/BASIC分类后扩展 `uv run pytest tests/services/test_upload_precheck.py tests/workers/test_extract_progress.py tests/workers/test_deembed_progress.py tests/workers/pipeline/test_extractor.py tests/services/test_managed_batch_paths.py tests/api/test_rename_batch.py tests/api/test_file_tree.py -q`：59 passed，无skip，证据 `/tmp/aln-b06-expanded-gate-0HMQwt/results.log`。全部DB/样本/cache/basetemp临时；Ruff通过。
- 扩展命令首次引用不存在的`tests/core/test_deembed.py`导致pytest exit4且无测试运行，已用rg定位实际tests/workers/test_deembed_progress.py再执行正确命令；不将空运行当通过。
- B06服务已完成；独立异步预检查job、draft报告持久化、HTTP prepare/commit由后续B07实现，当前尚未接入用户上传流程。B06标passed，下一B07。真实三端与两执行器最终验收未完成。

## B07 一次上传、异步预检与幂等消费通过

- 前置B06已通过。新增upload_draft_service、workers/precheck与HTTP prepare/draft/group/commit/recheck/delete接口；UUID草稿目录保留原文件名。摘要绑定输入、mapping、选项与目标版本，警告须完整确认。fresh Session锁下消费draft并创建batch/task/run，重复提交返回同一任务；已消费组恢复提供task摘要。
- SQLite BEGIN IMMEDIATE、PG行锁与request_key advisory lock覆盖并发prepare/commit。预检独立Celery/native类型任务；已知未投递与投递未知分开持久化，processing投递失败保留输入和唯一尝试。运行中删除先请求停止，写者检查确认后清理。commit复用已解压目录，表格使用预检生成的行文件；旧上传、监听、sync和表格入口复用同一检查器，去嵌标志仅随真实处理结果设置。
- SourceInput在run base捕获前登记，表格执行复用稳定UUID。修改upload_draft_service/upload_precheck、processing_run/source_identity、api/upload/sync、upload_service、extract_batch、local_queue/local_worker/dispatch、schemas/upload/config及相应测试。B09仍需替换活动Device直接写入与发布行为，B13负责保留产物清理；当前不宣称原子发布完成。
- 最终门禁：uv run pytest tests/api/test_upload_drafts.py tests/api/test_upload_target.py tests/services/test_upload_service.py tests/services/test_upload_precheck.py tests/services/test_processing_run.py tests/workers/test_run_dispatch.py tests/services/test_managed_batch_paths.py tests/services/test_source_identity.py tests/workers/test_extract_progress.py -q；123 passed，无skip，真实SQLite及专用PG schema，数据/cache/basetemp均临时。证据 /tmp/aln-b07-acceptance-89a2f6/results.log。Ruff和git diff --check通过。
- 中间不重复解压测试曾因测试缺SimpleNamespace导入2失败，留在B07修复后同门禁通过。HEAD与stage未改，无提交/push/部署/发布。B07标passed；B08 running。三端及两执行器综合验收仍待最终门禁。

## B08 持久条目与完整问题报告通过

- 前置B07 passed。新增processing_report.py提供record_item_result/record_issue/summarize_run：run+unit+member+规范port幂等条目、确定性问题ID、全量问题、持久file_counts/device_counts/issue_counts，计算成功与published_count分开。预检问题和拒绝表行保留，S2P双端口只算一个文件，表格一个文件与各行分开；未知历史报告unknown。终态未完成条目转换失败/取消并记原因。
- compute子进程只返回结构化结果，父worker临时spool限定内存并按块写入；pool完整返回前不写Device或计算问题，途中异常串行回退不会重复首个成功器件。成功/失败/跳过接入extract、compute、recompute、redeembed来源路径和两表格入口。原始成员/端口随生成曲线传递，全部问题持久化而不是failure_samples[:5]。
- TaskDetail增加run_id/quality_status/summary；GET report分页、issues.csv独立读取会话流式完整导出，防表格公式注入。服务/API测试真实SQLite/PG，1107问题逐条CSV对账及分页/重启可读。
- 原test_process_batch失效客户ZIP符号链接导致首轮51 passed/4 skipped，不计通过；保留链接，改本项测试临时生成20有效算法样本并独立SQLite/PG schema，禁止TRUNCATE用户库。这不是客户材料或真实异步执行器最终验收。PG管线发现旧MV异常未rollback导致任务终态写失败，已修复非致命刷新失败回滚。失败证据 /tmp/aln-b08-complete-Rv0zY2/results.log。
- 最终门禁：uv run pytest tests/services/test_processing_report.py tests/api/test_processing_report.py tests/workers/test_process_batch.py tests/api/test_upload_drafts.py tests/services/test_processing_run.py tests/services/test_upload_service.py tests/services/test_upload_precheck.py tests/workers/test_deembed_progress.py tests/workers/test_extract_progress.py tests/services/test_managed_batch_paths.py -q；108 passed，无skip。证据 /tmp/aln-b08-final-yDXSEN/results.log。Ruff通过。所有测试数据/cache/basetemp临时，HEAD/stage未改，无提交/push/部署/发布。
- B08 passed；B09 running，当前活动Device直接计算写入尚待B09替换，不宣称原子发布完成；三端及两执行器最终验收待后续。

## B09 staging与原子发布的独立功能门禁通过

- 新增result_publish.py：stage_device_rows仅写自身staging、UUID+payload白名单+摘要校验、批SQL幂等；PG单条upsert输入重复键曾7失败，改同块按UUID规范去重后14 passed，证据 /tmp/aln-b09-service-replay-oIfsZy/results.log。fresh Session/批次锁/SQLite immediate检查lease、active writer、result/source revision、删除/取消和mapping摘要；Device整数id/永久UUID保留，同事务文件树/路径/result_revision+1/task/run/active_run发布。重复发布不重复增加epoch或revision。
- recompute局部成功只更新所选指标，失败沿用旧值；旧批次全量处理任何预期失败拒绝发布，旧v4的revision=0但已有Device也按旧结果处理；首次partial允许，零成功拒绝。staging损坏、旧token、mapping变化、版本冲突、文件树后崩溃均不改变旧活动结果。Barrier真实双连接验证取消与publish仅一个赢家。
- extract/compute/redeembed/pipeline及异步/旧表格导入接入staging发布；新文件位于files/.runs/<batch_uuid>/<run_uuid>/<lease_token>/。预检目录复制给run管理但不再次解压；旧结果不移动或删除。生成曲线传原始成员和端口以复用UUID。pipeline保留原始输入和去嵌中间文件，避免处理器压缩后提前删除raw；文件压缩清理不在计算期执行。KEEP_RAW_ZIP仅成功发布后记录延迟CleanupJob，旧路径至少保留RESULT_FILE_RETENTION_HOURS=24，执行清理及读租约由B13负责。
- 历史兼容入口仅可给尚未启动的pending任务附加run，不能重置已执行尝试；租约装饰器拒绝重复执行，移除旧reset_task及worker活动Device删除/批更新/path赋值。processing_run捕获当前mapping摘要与base_has_results。pub后通知只读成功状态；PG MV维护CAS标记stats_epoch，失败记录可恢复问题，不回滚已发布结果。
- remap边界按分册末尾已定规则：当前仅kind契约；检查api/mappings及process未发现修改mapping时直接写Device，不开放不存在的remap入口/能力。
- 新test_result_publish真实SQLite/PG；更新test_pipeline_batch隔离fixture，禁止TRUNCATE用户库；worker conftest改临时文件SQLite供fresh lease连接。旧API/worker生成样本回归。最终指定及扩展门禁113 passed、2 skipped，证据 /tmp/aln-b09-final-KOpdaG/results.log。2 skipped是既有明确可选客户#2.zip用例，/Users/jingbozuo/Projects/#2.zip不存在，ALN-DATA中也未找到；该客户算法集成未验证，未计为pass。当前门禁只证明独立功能，不代替B21真实broker/native执行器及客户材料验收。
- 中间新增prepare_stage_directory缺Batch/get_settings导入导致2失败，已修复并重跑；不越过该失败门禁。Ruff/diff检查完成后进入B10；最终三端/两执行器验收仍未完成。

## B10 停止确认、迟到写屏障与独立心跳通过

- 前置B09独立功能门禁通过。新增processing_cancel/cancel_signals；请求仅写cancel_requested，确认队列移除、撤销未执行投递或自有进程join结束才cancelled。native join超时保留STOP_NOT_CONFIRMED，不删输入与staging；server记录extract/compute各Celery ID，非强制revoke，API重启不将活worker统一失败。死native进程证据可恢复中断任务。
- SSE按持久run/event_seq/status/stage/summary发事件，cancel_requested继续，Redis失败用数据库状态轮询。ProgressPublisher事件progress改为契约对象；成功通知只读已提交发布。条件写锁在提交前核验当前lease，旧worker无法覆盖新租约阶段。compute临时spool后分块写；recompute先缓冲证据再每1000条提交，实际SQLite/PG测试验证第一器件完成、第二器件计算阻塞时独立心跳仍更新，活动指标直到发布前保持旧值。表格两入口同样分块并独立心跳。
- 旧upload/sync登记后异常不再删输入；sync取消等待/失败重传/并发撞名不再删除任务历史或活动文件，返回可定位409。表格保留raw输入。扩展回归发现sync mapping只有sync://虚路径使真实预检查拒绝，修复为现有算法CSV格式的持久投影，并核验数值/PF可读。旧sync夹具dut1无mark更新为合法器件名，stub终态同步task/run而非单独task。
- 首次sync扩展1 failed/22 passed与合并128 passed/1 failed均留在B10，证据/tmp/aln-b10-sync-results.log及/tmp/aln-b10-final-results.log；未绕过门禁。最终命令uv run pytest tests/api/test_cancel_task.py tests/api/test_cancel_safety.py tests/workers/test_cancel_races.py tests/workers/test_run_dispatch.py tests/services/test_upload_service.py tests/services/test_result_publish.py tests/workers/test_local_worker_subprocess.py tests/workers/test_process_batch.py tests/api/test_upload_drafts.py tests/api/test_upload_target.py tests/api/test_sync_server.py -q，129 passed，无skip。真实SQLite/专用PG schema，所有样本/cache/basetemp/DATA_ROOT临时，证据/tmp/aln-b10-replay-results.log。Ruff及diff检查通过。
- 新保护快照/var/folders/fr/k3vpq27d5sd5zsy0ll16r_ym0000gn/T/aln-b10-resume-t38e6_z5；未stage/commit/push/部署/发布。以上是独立功能验收；真实broker/native执行器与三端前端综合门禁仍待B21/F16，不能声称总体完成。B10 passed，下一B11 running。

## B11 失败项幂等重试通过，首阶段检查点

- 前置B10 passed。新增services/processing_retry.py及POST /api/tasks/{id}/retry，严格mode=failed、request_key/expected_source_revision/item_ids。SQLite immediate与PG batch/advisory lock绑定全局请求键+请求摘要，相同请求仅一个新task/run，终态后重放仍返回原新任务，改请求内容409。仅原run failed/cancelled device/table_row可选择；无身份、缺输入明确拒绝，不猜UUID。
- 接受处理时保存完整频段/去嵌/类型/metrics配置摘要、算法代码摘要、实际原始包或源目录内容摘要与表格行sidecar摘要；retry同时校验mapping、配置、算法、版本、实际输入和校准。首次partial发布记录自身published_source_revision，避免把自己的发布当外部修改；外部数据变化409 RETRY_INPUT_CHANGED。重新提取明确核验原始包，不能仅凭未变活动目录判断包未变。旧不具备摘要的历史任务不伪造可验证身份。
- 新尝试保存retry_of/attempt/所选条目；复制成功staging和未重试问题作为继承证据，清除所选旧staging。compute/recompute/table只计算所选失败项；发布合并成功结果，保留器件整数id和UUID。首次partial与旧批次全替换门禁延续C04；复制父报告不修改父run/item/issues，报告显示继承成功数。重试的重试清除旧投递标记，HTTP并发/重放只投递一次；空队列回执按确认失败结束并保留记录和输入。
- 改动processing_run/result_publish/processing_report、workers compute_batch/reprocess_batch/precheck、api/tasks，以及upload_service首次deembedded=False（成功实际处理才发布标志）。新增test_processing_retry用真实SQLite/PG与Barrier验证同键并发、源/mapping/config变化、缺校准、原始包变更、sidecar篡改、取消后重试、终态HTTP重放与下一尝试；真实ZIP算法只补算第二器件，验证首个成功器件id保留及旧全量失败恢复；实际XLSX解析/表格worker复用UUID。
- 首批14 passed；补ZIP18 passed、HTTP20 passed；扩展139 passed。取消测试调用confirm_stopped漏keyword导致143 passed/2 failed，证据/tmp/aln-b11-final-results.log，修复测试调用后147 passed；后续guard/配置完善最终指定及扩展153 passed，无skip，证据/tmp/aln-b11-options-results.log。命令：uv run pytest tests/services/test_processing_retry.py tests/services/test_processing_report.py tests/services/test_processing_run.py tests/services/test_result_publish.py tests/workers/test_run_dispatch.py tests/workers/test_cancel_races.py tests/api/test_upload_drafts.py tests/services/test_upload_service.py tests/workers/test_process_batch.py tests/api/test_reprocess.py -q。额外HTTP/sync/report回归73 passed，证据/tmp/aln-b11-regression-results.log。全量Ruff及git diff --check通过。数据、DB、samples、cache、basetemp、测试日志均临时。
- 阶段检查点：预检draft、持久任务报告、安全停止、staging原子发布与失败项retry API已通过独立功能检查；当前前端仍是既有页面，G01/G02未接入，不能声称完整用户流程已交付。真实Celery/Redis/native subprocess与三端前端验收待B21/F16；无客户材料的既有集成仍未验证。
- 原始75资产摘要复查63 unchanged/12 changed/0 missing；本轮新增职责增量涉及原脏api/sync与upload_service，其余变化延续已记录B步骤。HEAD仍ab9bb94a6cc086920e1828c32ce2c75f96060263，stage为空；无commit/push/部署/发布。B11 passed；B12因下述冲突blocked，后续pending。

## B12 停止：Process工艺域没有批次关联

- backend分册135行要求Process也按Batch.deleted_at过滤；models/process.py 3行明确独立数据域，三模型没有batch_id/source_batch_id或关联。api/process.py 99/110行直接查询独立工艺表。无法按已定身份核验归属，不能凭run_no/名字匹配，也不能自行增加关联架构。
- 依据用户“遇到契约冲突或新增失败停在对应步骤给出证据”，B12停止于阅读/作用域核查，没有写B12业务代码，没有运行或声称B12门禁通过。已提交文本问题等待用户选择；耗时不视为确认。
- 具体可审阅解决方案和拟修订句子见platform-optimization-contract-review.md：推荐现有独立Process不受批次软删影响，所有Batch关联数据统一过滤，并验证删除/恢复不改独立工艺记录。如要求工艺随批次删除，需另补关联、历史映射和迁移契约。尚未替用户选择或修改contracts。

## B12 用户决策落地与有效数据作用域通过

- 2026-10-10用户确认独立Process不受批次删除影响，并授权自主修复可诊断代码/夹具失败。已同步contracts、backend及入口停止条件。保护快照aln-b12-resume-7n2xrflp延续原始75资产，未stage/commit/push/部署/发布。
- 新增batch_scope、batch_lifecycle、trash API：持批次锁核验pending/running/cancel_requested及未知active_run；软删保留30天，重复删除不推进修订，恢复保持UUID与器件整数ID。持久tombstone记录删/恢复修订，日志包含来源及影响；副本要求独立本地隐藏，不冒充远端删除。已清理恢复410。
- 批次列表/详情/器件、query/aggregate/distinct/stats、曲线/下载/文件树、CSV/XLSX/NN导出、服务与静态snapshot均过滤deleted_at；代理入口不能绕过删除检查，sync按名称读及upload目标拒绝回收站批次，v2目录ready=false/读取410。独立三类Process测试确认不变。NN与曲线遵循实际持久文件目录。
- 新增test_batch_trash/test_active_batch_scope与静态快照删除用例。夹具模型名/必填字段、查询路径/聚合响应字段错误已修复；扩展107 passed/1 failed揭示静态stats仍数全部devices，改为与导出df同一作用域后复测。指定门禁17 passed；扩展最终108 passed无skip，真实文件SQLite及专用PostgreSQL schema，证据/tmp/aln-b12-regression-results.log。Ruff全量与git diff --check通过。DB、数据、输出、cache/basetemp/log均临时目录。
- B12 passed，下一B13 running。F16/B21真实三端/执行器门禁仍未完成，不声明总体完成。

## B13 文件租约与可恢复物理清理通过

- 前置B12 passed。新增ArtifactLease模型及Alembic cf37b3290061；SQLite现有create_all升级路径纳入新表。run/download/export用途、owner、到期时间持久化；新读取、续租、purge都串行持同一Batch锁。处理独立心跳续文件租约，ASGI下载/曲线/导出从读取到完整响应或中断均持租约并在finally释放；释放失败仅到期解除，不伪造释放成功。
- 新增artifact_lifecycle：30天到期先提交CleanupJob意图和确切路径列表，后续同Batch锁内核验managed files/uploads root、源origin、symlink、共享引用、活任务及租约，执行IO后才标success/purged。IO失败保留failed/error与未清理tombstone；已部分删除阻止restore，running意图重启可恢复；重复执行收敛。恢复可取消未执行意图，不能恢复部分清理文件。旧结果保留24h，失败/取消后的raw输入保留。批次保留最小身份占位、来源登记和处理历史，只清除Device/FileNode及大staging payload，不级联丢失追溯。
- 原cleanup_service限定内部purge，不能直接删活动批次。现有Celery worker启用beat周期维护（仍五服务，不部署）；native startup/background同一purge服务。实服务组合最终验收仍归B21，没有以mock/eager替代。
- tests/services/test_artifact_lifecycle真实SQLite/PG验证权限失败、已提交意图的恢复、重复执行、外部路径/symlink、源身份、读租约/过期/释放所有权、24h旧结果、失败raw保护、恢复与清理双连接竞争；ASGI完整流/中断验证租约跨整个body，真实HTTP文件树下载回归。升级测试执行PG Alembic head与SQLite升级。旧cleanup测试改为活动批次必须保留；修正文件树独立夹具artifact engine后复测。中间126 passed/2 failed、补身份夹具16 passed/2 failed均已自主诊断修复，无跳过门禁。
- 最终命令uv run pytest tests/services/test_artifact_lifecycle.py tests/services/test_cleanup_service.py tests/api/test_batch_trash.py tests/api/test_active_batch_scope.py tests/models/test_platform_schema_upgrade.py tests/workers/test_run_dispatch.py tests/services/test_processing_retry.py tests/workers/test_cancel_races.py tests/services/test_result_publish.py tests/api/test_file_tree.py tests/api/test_devices_proxy.py -q，134 passed，无skip，1个既有Alembic path_separator弃用警告；证据/tmp/aln-b13-final-results.log。全量Ruff、git diff --check通过。数据产物/cache/log均临时。
- 原始75资产复核61 unchanged/14 changed/0 missing（新增原脏main/config的职责增量，前端保护延续）；无stage/commit/push/部署/发布。B13 passed，下一B14 running；仍未完成F16/B21/F17。

## B14 同步v3与显式来源冲突通过

- 前置B13 passed。新增v3 catalog、UUID resolve、本地隐藏清单/偏好、远端版本保护删除/恢复和显式table reconfirm；复用v2序列化与表格导入。同一读快照生成目录，完整协议/分页/重复来源/tombstone冲突先检查；目录缺席不删副本，owned同名来源不被覆盖。移除table-target自动更新版本；409保留原base，显式复核新request_key，修改已复核内容再次409。关联表提交与receipt在同一批次锁事务，重复键不同摘要拒绝。
- 原始上传补齐按实际ZIP成员、端口与内容摘要核验的source_ref/source_inputs传递，保留源origin/批次/器件UUID。客户端source_inputs用multipart JSON文件避免大型清单字段限制；无法精确核验旧来源不伪造身份。使用实际持久目录及原始ZIP，跨pack/upload持读租约；上传后逐UUID和count核验再标同步。origin表示来源出处而非当前机器，CleanupJob使用持久精确身份/路径意图核验，继续拒绝副本与外部目录。
- 改动api/replication_v3、schemas/replication_v3、services/replication_v3/source_transfer及sync/replica/desktop_sync/upload/extract、batch_scope/lifecycle、artifact_lifecycle/result_publish/server_client，新增对应服务与native tests。中间真实raw同步发现旧名称推导目录导致未投递，已改为Batch.file_path并复测；GET偏好漏HTTPException导入已修复。
- 最终命令uv run pytest tests/services/test_replication_v3.py tests/services/test_replication_v2.py tests/services/test_source_transfer.py tests/services/test_result_publish.py tests/services/test_artifact_lifecycle.py tests/services/test_processing_retry.py tests/services/test_desktop_sync_service.py tests/api/test_sync_server.py tests/api/test_upload_target.py tests/services/test_upload_service.py tests/api/test_batch_trash.py -q，183 passed，无skip，真实SQLite/专用PG schema，证据/tmp/aln-b14-final-results.log。uv run pytest -m integration tests/test_native_replication_v3.py -q，2 passed，两个真实SQLite/native HTTP执行器覆盖真实ZIP上传计算、UUID保留、软删恢复、隐藏、表格冲突与重复收据，证据/tmp/aln-b14-native-results.log。Ruff及git diff --check通过。
- 所有数据/cache/log在临时目录。未stage/commit/push/部署/发布；前端流程未接上，本步不代替F16/B21总体验收。B14 passed，B15 running。

## B15 统一查询规格与范围说明通过

- 抽取services/query_spec共用字段/过滤/排序验证，export_query不再依赖api.query，API保留旧私有符号别名；旧字段字典规范化AND树，非法空操作明确400。查询按用户排序后追加器件id，聚合按组最小id稳定排列。
- devices/aggregate/distinct在首条读取前进入SQLite显式BEGIN或PG REPEATABLE READ；同事务count/rows/来源修订/epoch，响应前独立连接检测来源或隐藏偏好改变并返回409 QUERY_SOURCE_CHANGED。缓存同时包含epoch与本地偏好摘要，隐藏不改变上游修订。每行_source_ref与用户选字段独立，legacy无发布证据保持unverified。
- 新ResultManifest、active_scope、epoch保留旧total/rows/groups。skip_count截断总数null/unknown，系统采样只记返回集合步进比率；精确count、NULL有效数及distinct全范围has_null明确，未完整读取distinct总数unknown。
- 新test_query_manifest/test_query_spec覆盖真实SQLite/PG、AND/OR、仅Batch字段、无结果、采样/截断、稳定顺序、NULL、缓存修订、本地偏好、双连接来源变化409。旧test_query_safety自己的无lifespan TestClient未初始化数据库，切换到现有隔离client夹具；UUID对象与str断言修正。初轮16 failed/27 passed留证/tmp/aln-b15-results.log，修复后69 passed，增量最终80 passed无skip。命令uv run pytest tests/api/test_query_manifest.py tests/services/test_query_spec.py tests/api/test_query_safety.py tests/api/test_query_sampling.py tests/api/test_active_batch_scope.py tests/services/test_replication_v3.py tests/services/test_data_revision.py -q；证据/tmp/aln-b15-final-results.log。全量Ruff和git diff --check通过。
- 数据/cache/log临时；未stage/commit/push/部署/发布。B15 passed，B16 running。新前端仍待F步骤接入，整体未完成。

## B16 停止：导出启动恢复状态冲突

- 前置B15/B10 passed。读取contracts C07第159行、C02第45行及backend B16第175行发现冲突：ExportJob同C02，cancel_requested只能确认cancelled；B16却要求所有未结束任务failed，pending也没有常规直接failed转换。API启动不能证明真实Celery写者停止。
- 已提供保留C02并确认停止、接管租约后恢复的具体方案，见contract-review；文本问题已发送，等待用户统一。没有写B16业务代码，没有运行或声称B16门禁通过，后续pending。
- B15完成后git diff --check通过，HEAD仍ab9bb94a6cc086920e1828c32ce2c75f96060263，stage为空，无commit/push/部署/发布。原始75资产摘要复核57 unchanged/18 changed/0 missing，未移除原文件；本轮新增query.py职责增量已写B15。全部数据产物临时。17/43步骤已通过，F16/B21/F17未验收，整体尚未完成。

- B16等待决策期间完成B15后真实native回归：uv run pytest -m integration tests/test_native_replication_v3.py -q，2 passed/9.90s，无skip，证据/tmp/aln-b15-native-results.log；查询快照/manifest增量未破坏两进程上传、同步与隐藏流程。未执行依赖冲突决定的B16工作。

- 2026-10-10用户确认B16推荐恢复规则，已同步C07和B16分册；冲突解除，B16 resumed/running。

## B16 独立导出任务与执行器门禁通过

- 用户确认恢复保持C02后已同步C07/B16。新增export_job_service/api/export_jobs/schemas/export_job/workers/export_job，ExportJob独立于UploadTask，规范QuerySpec/request_key摘要、捕获来源/epoch/偏好摘要、租约/执行器/心跳持久化，PG advisory+行锁/SQLite immediate保证同键并发一个任务；改请求409。
- local_queue新增LocalExportTask，local_worker使用明确类型handler map保留Processing/Precheck；服务器Celery单独导出task。领取/取消/终结核验token、状态与过期租约，旧writer不能发布。恢复仅dead native PID或明确stopped_executor_ids，pending经running到failed、running到failed、cancel_requested到cancelled，API重启不终止活跃/未知Celery。完整success产物仅白名单/未过期/完整落盘可下载，.part不公开；成功必须有效ResultManifest。
- 新Alembic 01fb348dec8e添加摘要/租约列，SQLite既有v5增量升级重复运行保留旧ExportJob。启动调用恢复，固定/export/jobs先于legacy路由注册；legacy导出继续原语义。B17完整写出尚未接入，此步不声称全量导出用户流程完成。
- 新test_export_jobs/test_export_dispatch验证真实SQLite/PG的持久状态、空CSV实际文件下载、过期/未注册文件门禁、并发同键、迟到worker、取消、死写者与未知写者。分发测试核对native typed task与Celery参数，不代替B21真实broker门禁。初次32 passed，扩展最终81 passed无skip、1既有Alembic警告；命令uv run pytest tests/api/test_export_jobs.py tests/workers/test_export_dispatch.py tests/models/test_platform_schema_upgrade.py tests/workers/test_local_worker_subprocess.py tests/workers/test_run_dispatch.py tests/workers/test_cancel_races.py tests/api/test_query_manifest.py tests/api/test_upload_drafts.py -q，证据/tmp/aln-b16-final-results.log。Ruff --no-cache及git diff --check通过。
- 数据/文件/cache/log临时，无stage/commit/push/部署/发布。B16 passed，B17 running；整体18/43通过，F16/B21/F17未验收。

## B17 固定来源的完整流式导出通过

- 新query_snapshot/export_writer：首条读取前PG只读REPEATABLE READ或SQLite显式BEGIN，同快照来源目录/epoch/count/SQL稳定顺序、2000行cursor分块，finally关闭；不使用图表limit/sampling。独立job心跳和一个批次文件租约续期线程，不在读事务提交心跳。开始/完成核查捕获的完整来源和epoch/本地偏好；success事务还锁确切origin+batch对及epoch，源变化失败不公开文件。
- CSV data.csv+manifest.json+data.zip，XLSX write_only说明页与data_N，每页1048575数据行。实际输出字节限制含所有产物；行数超限明确失败，字符串公式防护且负数保持数值，非有限值转空并记录计数，manifest不含NaN或绝对路径。独立token暂存目录，取消/IO/预算/源变更删除part，success仅完整最终文件白名单。
- 初次PG夹具单事务20万行触发器写入明显缓慢，238.15s仅SQLite CSV通过后定向中断，不计全门禁；仅将夹具准备改为2000行提交，产品发布原子性不改。第一次完整40 passed但超字节XLSX异常留下ZipFile析构警告；改ExcelWriter使用显式ZipFile上下文finally关闭，以-W error::pytest.PytestUnraisableExceptionWarning复测，40 passed/77.05s，无skip无警告，证据/tmp/aln-b17-acceptance-results.log。
- 指定命令uv run pytest tests/services/test_full_export.py tests/api/test_export_jobs.py tests/api/test_export.py tests/workers/test_export_dispatch.py -q；两真实数据库分别CSV 200001行逐ID对账，SQLite实际1048576行XLSX用openpyxl重新读取，data_1=1048575、data_2=1，ID集合完整。新来源/指标修改、finish前变更、取消、IO、行/字节配额、公式字符串与数值检查。旧export测试改复用隔离client。新增确切来源对锁测试和最后资源/失败分支复测20 passed/3 deselected，证据/tmp/aln-b17-pair-results.log（deselected是已由完整门禁验证的大数据测试，并非缺环境skip）。
- 影响查询/本地偏好锁/租约/升级的扩展72 passed无skip、1既有Alembic警告，证据/tmp/aln-b17-regression-results.log。当前代码真实两native HTTP执行器上传计算/同步/创建export→查询终态→CSV实际下载2 passed/10.82s，证据/tmp/aln-b17-native-results.log。Ruff --no-cache及git diff --check通过。此步不代替B21真实PG/Celery broker执行与性能总门禁。
- 全部大数据、DB、CSV/XLSX/ZIP、cache/log临时；无stage/commit/push/部署/发布。B17 passed，B18 running；19/43通过，整体未完成。

## B18 同口径批次比较通过

- 提取条件必须有成功发布run、配置/算法/映射摘要及当前行与发布payload的对账证明；未知来源和表格修改旧指标明确拒绝源比较。两组必要列各上限200000，单位、频段和结构条件明确检查，共同子集需显式选择。numpy linear分位数、ddof=1、有效/缺失n和规则unknown；数值溢出不产生非JSON值。共用静态夹具platformComparison.json。
- 最终96 passed，无skip，/tmp/aln-b18-final-results.log；最新真实native上传/发布/导出/源自比较2 passed in 10.31s，/tmp/aln-b18-native-results.log。Ruff与diff通过。B18 passed，B19 running；20/43通过，整体未完成。

## B19 能力与同步回执通过

- capabilities按实际数据库、初始化表/来源身份、原生线程或Celery响应判断；desktop远端删除需独立capabilities+完整v3目录握手，credential_storage=false。新增sync-status与sync_operations.expected、PG迁移/现有SQLite增量升级；原synced_at无receipt显示unknown。请求原始revision/key/digest保留，独立目标GET后UUID/version/完整可携值摘要匹配才记录receipt。
- 原始上传、v3拉取、表格回传、网页推送接入持久过程。网页仓库写成功pending，发布旧版本pending，断网unknown，实际快照匹配confirmed；不持久化token。分享包保留source UUID。关联表格回传按既有30天软删保留本地行/输入/identity/target证明，不物理删除；409不重基。
- 指定及扩展命令pytest tests/api/test_capabilities.py tests/services/test_sync_status.py tests/services/test_desktop_sync_service.py tests/services/test_replication_v2.py tests/services/test_replication_v3.py tests/services/test_web_sync_service.py tests/models/test_platform_schema_upgrade.py tests/web/test_make_share_pack.py：97 passed，1既有Alembic warning，无skip，/tmp/aln-b19-final-results.log。最新真实native SQLite server+desktop：2 passed in 10.57s，/tmp/aln-b19-native-results.log，含真实能力握手、原始内容receipt与CSV下载。网页外部发布未执行；延迟发布通过临时夹具验证状态规则，最终两执行器门禁归B21。
- 原始75资产摘要复核：{'unchanged': 56, 'changed': 19, 'missing': 0}；stage为空，HEAD未变；新增脏文件修改均为本步增量，数据/DB/ZIP/日志在临时目录。B19 passed，B20 running，21/43通过，整体未完成。

## B20 模型已有产物元数据通过／后端第二阶段检查点

- 新ModelMetadata schema与只读服务，按当前权重/配置/baseline/归一化摘要描述模型，LRU以实际文件mtime/size失效；训练范围仅来自明确train分组，不以测试集或归一化均值猜测。实测/仿真标签仅读取有证据的显式来源；缺失unknown。旧report保留实有指标但标unverified；当前模型与真实数据包摘要匹配才verified。非有限指标null，不复制targets冒充指标。predict增加within/out_of_range/unknown说明，旧status/predict字段保留，native torch禁用仍安全降级。
- 未来现有评估脚本记录实际所用stage2（含显式override）与数据包摘要；未重新评估/训练。ONNX导出写同schema，静态surr_meta只接受对应来源模型摘要的ONNX描述，缺失不推断模型版本。未向public生成模型或数据产物。
- 指定及扩展pytest tests/api/test_model_metadata.py tests/api/test_surrogate_api.py tests/api/test_surrogate_prewarm.py tests/web/test_web_snapshot.py：17 passed，3既有torch结构warning，无skip，/tmp/aln-b20-results.log；Ruff/diff通过。
- B01–B20后端基础实现与分步门禁完成；前端尚未接入新上传/报告/项目/比较/导出/回收站流程，因此不宣称用户流程已交付。真实PG/Celery/Redis综合流程、Electron进程重启、静态实际查询和最终性能门禁仍待F16/B21。B20 passed，F02 running，22/43通过，整体未完成。

## F02 工作表来源与局部账本通过

- AnalysisDataset增加manifest/provenance/edits/exclusions；SourceRef校验持久UUID与revision，未知来源保留null且未验证，tuple使用JSON数组。路径/显示名不影响源身份，源身份字段不可直接编辑。loadQueryRows第三参数兼容旧调用；dataset UUID防重启撞号。复制新工作表rowId/账本归属，源UUID不变。编辑记录前后值，排除保留原行和原因；现有undo/redo恢复整个账本。
- npm test -- provenance.test.ts dataset.test.ts workspace.test.ts：17 passed；npm run typecheck通过，/tmp/aln-f02-results.log与/tmp/aln-f02-typecheck.log。临时前端副本/var/folders/fr/k3vpq27d5sd5zsy0ll16r_ym0000gn/T/aln-platform-frontend-hf6vtv6f/frontend先构建Electron声明供既有TS project references使用；首次typecheck缺声明TS6305已诊断补建，所有输出在临时副本。F03 running，23/43通过，整体未完成。

## F03 项目序列化与运行时校验通过

- ProjectHead/Version/Backup按C06与前端2.1；复用DatasetRegistry、PointStyleState及统一SourceRef。serializeWorkspace保留配置/行/来源/账本/备注/块序列，restoreWorkspace仅complete并清空selection/history。运行时校验格式/未来schema/日期/generation指针/registry顺序/行ID唯一/列/点样式引用/chunk数量与唯一/账本/manifest/来源对账，不对unknown用类型断言。100MiB与500000行预算；凭据/HTML/script/expression字段及非JSON对象拒绝，非有限数null+warnings；version未知字段归controlled extras。
- 旧Explore配置只迁移已知轻量字段，包含旧{name,agg}轴选择，不能声称恢复丢失行。首次排除恢复因对象键顺序产生假冲突，改为递归按键排序的语义对账并补重排键测试；损坏块和伪造row来源均拒绝。
- 临时前端副本npm test -- projectCodec.test.ts provenance.test.ts workspace.test.ts dataset.test.ts：24 passed；npm run typecheck通过。证据/tmp/aln-f03-results.log与/tmp/aln-f03-typecheck.log；所有产物临时，diff检查通过。F03 passed，F04 running，24/43通过，整体未完成。

## F04 真实IndexedDB分块保存通过

- ProjectStore与IndexedDbProjectStore：固定数据库/stores、2000行块、SHA256与位置对账；未变块复用，单格编辑仅写所在块。先写draft/chunks，最后同一readwrite事务核验generation、确认所有引用块存在、写complete及head；complete事件才成功。quota/abort/conflict保留旧head，snapshot不能原地保存。load只读完整版本、核验块摘要/顺序/归属及F03完整结构；versionchange关闭连接，未知DB版本拒绝。维护事务保留当前和上一complete并按引用清理draft/orphan，维护抢占草稿时保存明确失败而不能切损坏指针。
- npm test -- IndexedDbProjectStore.test.ts projectCodec.test.ts provenance.test.ts workspace.test.ts dataset.test.ts：26 passed；typecheck通过。npm run test:e2e -- projects.spec.ts：5真实Chromium流程通过，含2001行两块/单格复用/刷新、同generation双写者竞争、真实IDB中止+注入QUOTA、损坏块、缺失版本与未来DB版本。证据/tmp/aln-f04-{unit,e2e,typecheck}.log。
- 首轮浏览器测试URL缺baseURL及macOS /var→/private/var临时目录allowlist不匹配已诊断：修复测试baseURL与仅临时Vite配置后重新完整通过；未更改仓库Vite配置。Browser IDB验收不等于Electron进程重启验收，后者仍归F16。F04 passed，F05 running，25/43通过，整体未完成。

## F05 自动保存与配置所有权通过

- workspace拥有AnalysisSpec、notes/extras，restoreProject恢复complete及空history/selection；row undo不回退后来修改的图形配置/备注。Explore配置退出PageState，旧配置一次读取，成功IDB complete之后写迁移轻量标记，PageState后续不重复存分析配置；原始行不凭localStorage声称恢复。来源/行命令与现有撤销体系复用。
- useProjectAutosave：1000ms、单写队列、saved只对应已commit捕获状态，新编辑继续dirty；selection/hover不写。启动恢复/会话模式/dirty时间/上次保存/QUOTA等明确提示；beforeunload仅提醒，不承诺异步写入完成。多窗口CONFLICT暂停自动写，仅重载其他窗口版本或另存新UUID。notes/controlled extras跨保存保留。
- typecheck、全npm test、npm run build通过；144 passed/1既有skip（不作验收pass），/tmp/aln-f05-{typecheck,unit,build}.log。真实Chromium项目验收9 passed，/tmp/aln-f05-e2e.log；含恢复编辑/排除/撤销/config、选中不保存、Barrier控制保存期间新编辑、QUOTA保留UI输入与旧complete、双窗口冲突真实UI。首次冲突UI测试未等待另一窗口commit而读到旧“已保存”标签；改为实际generation读回门禁后完整复测通过，未更改CAS规则。
- 构建/浏览器profile/数据库/trace均临时副本。Electron真实进程重启及静态实际查询仍待F16。F05 passed，F06 running，26/43通过，整体未完成。

## F06 项目入口、快照与JSON备份（2026-10-10）

- 新建/打开/命名/另存/模板/固定快照/导入导出接入Explore现有provider。脏切换提供保存/放弃/取消；切换等候在途写入；不删除旧head。固定快照reducer与存储层均禁止原地修改，另存恢复可编辑。模板移除固定source/batch引用及比较配置，应用前校验字段/单位。
- 备份只导出complete；dirty须先保存。IDB不可用时明确为未持久化会话文件备份。全量校验后导入新project/version/chunk UUID，保留dataset/row/source；未来版本、凭据、原始曲线拒绝，零写入。
- 临时副本`npm run typecheck`、`npm test`（149 passed、1既有skip）、`npm run build`、`VITE_STATIC=1 npm run build`通过；`projects.spec.ts`11 passed（22.3s）。日志`/tmp/aln-f06-{typecheck,unit,build,static-build,e2e}.log`。
- `projectTransfer.spec.ts`真实浏览器→VITE_STATIC实际bundle→实际Electron主进程→浏览器JSON互导1 passed（7.4s）；逐端验证rows/spec/provenance相同、新projectId且敏感字段扫描无命中。Electron的HOME、userData、下载路径均系统临时目录；未启动个人后端。首次Playwright Electron download事件不适用，修为真实session will-download并落盘核验，复测通过。日志`/tmp/aln-f06-transfer.log`。
- `git diff --check`通过，未stage/commit/push/发布；F06 passed，27/43通过。此三端验收针对项目JSON功能，未冒充F16最终产品流程。

## F07 查询范围与异步隔离（2026-10-10）

- `resultManifest.ts`校验行数、匹配计数、完整/截断/采样关系、查询规格及verified器件来源版本；旧API总数保持未知、来源未验证，不把旧LIMIT+1计数当精确总数。聚合返回组与匹配器件计数分开；本地修改/排除继续使用既有ledger。
- Explore三条查询分支接入同一RequestSequence；迟到结果/错误/结束状态都不能覆盖较新请求，配置或项目切换使在途请求失效。来源变化409明确要求重新查询，保留上一工作表。
- 静态DuckDB读取LIMIT+1并按独立COUNT返回matched_total；skip_count超限为unknown，正好达到limit不误判截断。加载Arrow前后、查询前后核对发布manifest版本；变化拒绝并要求重载。旧静态产物缺少来源证据仍unverified；未把纯SQL计数或mock冒充来源验证/最终DuckDB验收。
- `DataScopeBanner`显示总数未知、受限、采样、未验证及local edit/exclusion；当前工作表CSV旧语义保留。新增create/get ExportJob只消费C07路由，静态在HTTP前拒绝调用；完整导出交互仍由G04完成。
- 最终临时副本typecheck、全量unit（157 passed、1既有skip）、普通build和VITE_STATIC build均通过；`/tmp/aln-f07-{typecheck,unit,build,static-build}.log`。新增单元验证慢A/快B、损坏计数、legacy采样、静态LIMIT+1/count及聚合组；静态SQL适配单元使用mock，真实DuckDB共同夹具仍是F09门禁。
- 保护核查75路径：54原hash保持、21为此前授权步骤增量变更、0 missing；清单`/var/folders/fr/k3vpq27d5sd5zsy0ll16r_ym0000gn/T/aln-f07-protection-qrf_1ixc/audit.json`。HEAD仍ab9bb94a6cc086920e1828c32ce2c75f96060263，stage为空，diff --check通过。F07 passed，28/43通过。

## F08 契约阻塞证据与待确认方案（2026-10-10）

- F08/前端2.2要求计算前核验端口、结构、单位、频段及处理摘要，每差异显示具体值，未知不能当相同，错误保留输入且不发计算。C05与B18只定义POST `/api/analysis/compare`计算端点。
- 实际`backend/app/api/comparison.py`只有计算路由；`schemas/batch.py::BatchDetail`只有频段/去嵌/工艺，无algorithm_digest/mapping_digest/config_digest。`api/tasks.py::get_task_report`返回summary/issues，无这些证据。`services/comparison_service.py::_conditions`在服务器内部读取已发布ProcessingRun证据；`compare`在读取cohort后校验摘要并计算统计。通过调用计算API来获取条件会违反F08的先审查要求，禁止以前端默认值或相同空摘要绕过。
- 已向用户提出：增加只读条件接口，复用B18当前来源/已发布结果证据校验及固定版本快照；或保持源比较禁用、仅工作区快照比较。尚未修改对应契约或实施F08。
- 推荐接口沿用ComparisonRequest/SourceRef，单一只读快照返回各来源版本、条件（frequency/deembedding/process/config/algorithm/mapping摘要）、实际端口/结构集合、FieldMeta单位与显式issues，不计算统计、不更改源数据。静态端需要发布JSON承载同形条件证据，不能请求服务器API；旧产物缺失必须unknown。确认后先更新C05与分册，再实现并验证SQLite/PG及临时静态产物。
- 这是现有任务书与可消费接口的契约缺口，非环境缺失或测试失败。F08停在此处；F08–F17/G01–G04/B21整体尚未完成，未声称最终三端/两类执行器验收通过。未提交/push/部署/发布。

## F08恢复：用户确认C05a（2026-10-10）

用户选择“补充只读条件接口并更新契约”。已增补contracts C05a及后端/前端分册；按SourceRef固定版本、证据复用、只读无统计、静态无HTTP约束推进。先执行新增SQLite/PG接口门禁，再接前端。

## F08 只读条件与兼容审查通过（2026-10-10）

- C05a及两分册已根据用户授权更新。新POST compare/conditions固定同一快照，复用B18_cohort当前指标及发布条件证据，不返回rows/statistics、不调用统计函数、不写数据库；末端独立版本核对。unknown/超预算明确issues，过期SourceRef409。原计算接口仍再次验证。
- 新comparisonSpec/compatibility/ComparisonSetup消费具体条件、单位、完整结构端口元组；不同UUID同显示名称不合并，来源版本未知禁止源模式，未知条件不当相同。频段/摘要/单位差异阻止；共同元组必须人工勾选，不拼虚假笛卡尔积；null结构用is_null过滤。输入及旧配置在错误时保留，仅确认后更新AnalysisSpec。工作区模式明确局部数据范围、不请求条件HTTP。旧静态证据缺失仍禁源模式，F09处理新产物证据与实际DuckDB对账。
- 新增API/服务门禁真实SQLite+PG：34 passed，无skip，`/tmp/aln-f08-backend.log`；包含不调用statistics/_summarize、篡改指标证据unknown、具体S11/S22及旧版本409。Ruff全量与diff --check通过。
- 临时副本typecheck及unit：164 passed、1既有skip，`/tmp/aln-f08-{typecheck,unit}.log`；包括UUID来源、自比较、未知、频段/单位、完整tuple交集、旧版本/坏摘要、UI错误保留输入且不应用规格、快照无HTTP。F08 passed，29/43通过；未提交或发布。

## F09 完整比较与实际DuckDB对账通过（2026-10-10）

- 新统计纯函数使用有效数n、缺失n、线性分位与样本标准差，缩放求和避免极值溢出；差值为相同组B.mean-A.mean，不能伪配对。自定义规则unknown不进入通过率分母，与原始pf各自独立。极值差值溢出改为null并补JSON严格测试，后台亦修复相同可诊断问题。
- 源模式调用单次B18compare，核对完整verified manifest、来源版本/唯一器件UUID、实际行数、无截断/采样，以及服务端/客户端统计容差；任一cohort失败不返回半套。快照模式同步捕获现有rows/ledger/manifest，不把partial结果称完整。
- C05a静态产物已补齐：既有export_all只读SQLite事务中复用condition_proof，逐行核对当前结构/指标与发布payload/digest，只提供真正proven_metrics；gzip/原始Arrow两摘要绑定实际文件。静态读同QuerySpec count及2000行块，200000行/100MiB公开限额明确拒绝；来源/发布版本变化拒绝，不用queryDevices图表cap替代完整比较。旧证据缺失仍禁源模式。
- 真正VITE_STATIC bundle在Chromium内运行DuckDB-WASM，与同一真实SQLite处理证据夹具的服务结果对账：统计、合格率、pf、manifest与SourceRef一致；超预算和发布manifest变化拒绝，3 passed（2.9s），`/tmp/aln-f09-e2e.log`。预算用例只修改隔离测试的内存表以注入超限，未更改发布文件或用户数据。
- 实测发现Vite preview发送Content-Encoding:gzip、fetch自动解压；已按实际gzip魔数选择对应摘要/按需解压，消除双解压并保持文件完整性校验。未改用户Nginx/部署设置。
- 临时副本typecheck、170 passed/1既有skip、普通与static build通过，`/tmp/aln-f09-{typecheck,unit,build,static-build}.log`。SQLite/PG比较及静态导出回归41 passed，`/tmp/aln-f09-backend.log`，包含篡改指标不发布证据；全量Ruff/diff检查通过。所有生成DB/Arrow/JSON/build/trace都临时路径。F09 passed，30/43通过，未提交或发布。

## F10 对比图与同一选择/撤销历史

- 增加 ComparisonView/comparisonScene及场景测试；Explore挂载分布、B−A共同分组均值差、按wafer分面的均值图。源比较成功后两cohort加入现有registry，工作区比较复用原表；失败保留上次结果，新规格/规则/项目切换使旧请求失效。图customdata保留datasetId,rowId，重复坐标mean+n，统一有限色域，等值padding，全部缺失无色域。
- 现有selection新增显式addAcross模式与按表读取助手；普通add/replace行为保留。跨表删除是现有batch命令、同一history，一次undo恢复两组；没有比较专属selection或第二套撤销。Explore/Worksheet/Charts消费按表选择。
- npm run typecheck通过；npm test 174 passed、1既有skip；npx playwright test comparison.spec.ts真实Chromium/Plotly/WorksheetGrid 1 passed，检查差值点击两表各3有效行、排除各留1缺失行、图点消失、一次撤销恢复、分布点点击回到单表选择。首次测试被Plotly正常draglayer拦住SVG定位点击，改实际屏幕坐标鼠标点击后通过；未模拟plotly事件。
- npm run build与VITE_STATIC=1 npm run build通过；证据/tmp/aln-f10-{typecheck,unit,e2e,build,static-build}.log。所有产物在隔离临时前端副本。git diff --check通过，HEAD未变、stage空，无提交/push/部署/发布。F10 passed，31/43；下一F11 running，最终三端/双执行器门禁尚未完成。

## F11 冻结报告与离屏图片

- reportModel从validateProjectBackup校验后的complete版本深拷贝并递归冻结；统计/manifest/备注/QuerySpec/SourceRef均绑定该版本，当前图及比较图从冻结rows创建场景。源比较未完整载入拒绝报告；20图上限不静默删图。未知总数/采样/截断/本地修改排除/未验证来源醒目标为部分工作区。
- 抽取既有WaferMap的buildWaferSpec供界面及报告复用，保持相同分面、颜色及悬浮信息；没有新图表库。plotImages复用同Plotly导入，1600×1000 scale=1逐图离屏newPlot/toImage，finally purge并删除DOM；任一失败或PNG累计100MiB超限整体reject，不创建download。
- npm run typecheck通过；npm test 177 passed、1既有skip；测试覆盖complete指针、后续修改隔离、深冻结、valid/missing n、newPlot及第二图错误完整拒绝、DOM清理、外来URL拒绝、超图预算。npx playwright test comparison.spec.ts真实图表/工作表/wafer抽取回归1 passed。证据/tmp/aln-f11-{typecheck,unit,wafer-regression}.log；真实PNG/离线打印门禁在F12执行。git diff --check通过，无stage/提交/push/部署/发布。F11 passed，32/43，下一F12 running。

## F12 自包含HTML与已有模型证据

- reportHtml/ReportDialog接入Explore，saveNow后按返回的complete指针读取并冻结，版本已变化明确失败；全部图成功才生成下载。标题/备注/来源/统计/QuerySpec/单位/规则/模型说明全部escapeHtml；CSP禁脚本及外连，仅允许PNG data和内联打印样式。图片数与冻结图严格一致，容量失败不省略图；摘要分开展示原始pf与自定义规则及valid/missing n，比较摘要不嵌入重复全量rows。
- Surrogate及静态ONNX只消费已发布metadata；缺版本/范围/指标显示未知，未经数据集digest证据的verified降为unverified，越界明确提示，不新训模型、不编造置信区间。
- npm run typecheck、npm test（180 passed/1既有skip）、普通/静态构建通过。npx playwright test report.spec.ts comparison.spec.ts 2 passed：真实Plotly生成1600×1000 PNG，下载实际HTML后新offline浏览器打开，图片完整、HTTP请求0、恶意备注纯文字/window.evil未定义，print media无横向溢出、实际PDF MediaBox A4横向；比较真实联动回归通过。模型服务不可达时报告明确未知，并非模型验收通过。证据/tmp/aln-f12-{typecheck,unit,e2e,build,static-build}.log，HTML/PDF在系统临时aln-report目录，所有构建隔离。git diff --check通过，无提交/push/部署/发布。F12 passed，33/43；下一F13 running，最终F16/B21/F17待验收。

## F13 能力与持久目标状态

- api/capabilities静态优先返回固定DuckDB能力，不看Electron壳、不探测后台；其他模式严格校验GET /capabilities，失效清为未知、提示重试。App状态栏显示真实role/database/executor能力。client在实际运输前拒绝未知/静态服务器写及明确关闭的feature；只读POST查询、比较、预测、原曲线ZIP不误挡，XLSX独立能力。未根据health推断。
- syncStatusAdapter/SyncStatusBadge按资源分别读取server/web后端持久字段，独立展示confirmed/pending revision、时间及error_code；旧连接状态仅表示连通/待回传，不造confirmed证据。BatchDetail显示并可人工只读刷新；请求迟到及卸载防护。
- npm run typecheck、npm test 184 passed/1既有skip、普通与静态build通过。单测覆盖server+SQLite、协议缺字段、未知实际API写transport未调用、静态caps及状态0后端请求、targets区分、旧status不伪造确认、已有失败码保留。证据/tmp/aln-f13-{typecheck,unit,build,static-build}.log。git diff --check通过，无提交/push/部署/发布。F13 passed，34/43；下一F14 running，真实状态端到端仍归F16/B21。

## F14 设置与单一同步协调器

- Settings路由及Sidebar入口，Batches仅链接设置，不再创建ServerSyncPanel；连接/同步/人工网页发布/项目存储/偏好分区。凭据仅React会话state，重开为空；发布须勾选人工确认，提交只显示等待实际快照确认，不真实发布。
- useSyncCoordinator复用原ServerAutoSync唯一60s轮询与online/focus/local-change事件。自动/手动/回传/发布共享inflight互斥；相同sync合并，异种操作拒绝重叠。409及partial conflict停止后续自动写；启动读取待回传资源的后端持久冲突，重开不绕过冲突；人工刷新仅GET，不重基revision。最后owner卸载后停止timer/events并以epoch拒绝迟到后续写/状态。
- npm run typecheck、npm test 189 passed/1既有skip、普通/静态build通过；旧ServerAutoSync 8项行为保留。新增测试：重复同步、单timer、多owner、卸载迟到status不写、offline恢复、持久/partial409阻止重试、static0HTTP。真实Chromium settings.spec.ts 1 passed（本地HTTPfixture，不真实发布），验证首次自动同步、partial409后只读刷新+online不重复写、token重开空且localStorage无token。首轮fixture路由误拦src/api模块，缩小为/api路径后通过。证据/tmp/aln-f14-{typecheck,unit,e2e,build,static-build}.log。
- 原始75保护摘要：47 unchanged/28 changed/0 missing；新增7原脏路径均为本阶段App/ServerAutoSync/ServerSyncPanel/BatchDetail/Batches及确认契约职责增量。audit=/var/folders/fr/k3vpq27d5sd5zsy0ll16r_ym0000gn/T/aln-f14-protection-rkb5k0vz/audit.json。HEAD不变，stage空；无提交/push/部署/发布。F14 passed，35/43；下一F15 running，整体尚未完成。

## F15 工作台真实汇总与最近项目

- useRecentProjects按实际lastOpenedAt取5；IndexedDbProjectStore.markOpened只CAS更新head打开时间，不切换version、不增generation、不写chunks。启动/明确reload后更新，Explore接projectId路由，先保存当前dirty版本再读取complete；失败保留当前工作区。
- Dashboard统计、任务、项目与AttentionList独立加载；异常任务读取最近50项，质量读取最近10任务既有detail.summary/quality_status，同步读取v3目录近期10项及后端目标状态，范围明确。无API/失败显示未提供/未知，不假0或吞失败。同步项以UUID来源+target为ID；项目源批次先origin/batch UUID resolve再导航，404保留快照。静态隐藏上传/任务入口，显示项目、发布版本及只读模型未知/证据。
- npm run typecheck、npm test 192 passed/1既有skip、普通/静态build通过。Dashboard/AttentionList单测覆盖统计失败任务与项目仍可读、质量链接、同步失败独立、源404保留、最近5顺序。npx playwright test projects.spec.ts settings.spec.ts 13 passed，实际IDB验证markOpened未改版本/generation/chunks、旧version拒绝，既有全部项目恢复/冲突/导入及设置回归通过。证据/tmp/aln-f15-{typecheck,unit,e2e,build,static-build}.log。git diff --check通过，无提交/push/部署/发布。F15 passed，36/43；下一G01 running，最终性能/三端/执行器门禁待执行。

## G01 多文件预检查与真实入库验收

- 新UploadGroup/useUploadDrafts/PrecheckReport/IngestionReport接入Upload、TaskDetail与Floating。每文件独立request_key，URL及轻量偏好恢复group；不保存File或凭据。校验零字节/重复文件元数据/有限频段，展示双端口、全部预检问题、确认警告、fatal阻止提交；原摘要commit不重传，过期与设置/证据变化必须复查和重新审查。每1s读取互斥，卸载停止；全部结果保留，partial独立说明文件/器件/实际发布数，分页问题及完整CSV可定位。移除Upload旧自动跳转/自动网页发布，人工发布复用设置入口。
- 临时前端副本npm run typecheck、npm test通过：197 passed/1既有skip；过期复查、警告重置、fatal禁提交、双击幂等、刷新无File恢复、PRECHECK_STALE不重传不自动提交均覆盖。普通及VITE_STATIC=1 build通过。日志/tmp/aln-g01-{typecheck,unit,build,static-build}.log。
- npx playwright test upload-flow.spec.ts真实Chromium+隔离native SQLite API/本地执行器1 passed（10.9s）：两有效文件发布2/1器件，一个坏文件fatal；刷新后两次dblclick仍只3次prepare/2次commit，组验收单不自动跳转。实际SQLite保存上传与原始夹具SHA256一致（5414/4155/153字节）；所有DB/zip/截图/build均临时目录。首轮Playwright route.fetch丢multipart字节，修复测试运输为临时Vite直接代理后通过；未弱化后端校验。证据/tmp/aln-g01-e2e.log，服务根/var/folders/fr/k3vpq27d5sd5zsy0ll16r_ym0000gn/T/aln-g01-native-1w363gv_。
- 原75保护路径46 unchanged/29 changed/0 missing；相比F14新变化仅Upload.tsx，保留文件选择/表格/对照表/去嵌功能并替换其直接上传流程，属于G01授权增量；审阅原临时基线副本。audit=/var/folders/fr/k3vpq27d5sd5zsy0ll16r_ym0000gn/T/aln-g01-protection-pgwf37_z/audit.json。git diff --check通过，HEAD仍ab9bb94，stage空，无提交/push/部署/发布。G01 passed，37/43；G02因下述契约缺口blocked，整体未完成。

## G02 可重试项读取契约缺口（待用户确认）

- flows G02要求只列report可重试项、提交原expected_source_revision、展示retry_of链接；C04只规定POST retry写接口。api/tasks.py:74当前report只有summary/分页issues，schemas/task.py:10详情只有run_id/quality_status/summary。问题item_id既不覆盖所有失败/取消项，也不能证明该项unit_kind或源身份。
- processing_retry.py:101所需基准为published_source_revision或base_source_revision；后者未通过详情/报告公开。:146按device/table_row且failed/cancelled筛选，随后必须证明SourceInput身份。前端不能用当前批次版本替代旧任务基准，不能从错误文案猜测可重试项，也不能由UUID猜父任务ID。
- 建议仅补充只读GET /api/tasks/{id}/retry-options?offset=0&limit=100，返回task_id/run_id、expected_source_revision（原任务基准，未知null）、retry_of_task_id（原父run对应task，未知null）、分页items及next_offset。items包含id/unit_kind/source_member/port/status/error_code，使用B11同一筛选/可核验源身份规则；不返回绝对路径、输入内容或token，不修改revision、不创建任务。旧任务返回未知及空项并明确不支持安全重试。409由现有POST继续校验输入/配置/版本，前端保留选择且不自动换基准。
- 确认后先同步C04/B11/G02契约，补SQLite+PG读取测试，再实现状态/SSE/停止重试界面和对应测试。目前未改G02业务代码。停止来源为用户要求的契约冲突停止规则；已发起上述具体方案确认。G03/G04/F16/B21/F17仍pending，三端与两执行器总体门禁未通过。

## G02 停止与失败项重试通过

- 用户确认只读retry-options，已同步C04/B11/G02补充契约。接口分页稳定ID、原run基准版本、父任务ID及可证明身份的失败/取消器件/表格行；旧记录未知为空，不改版本、不创建任务。B11共享筛选规则，POST仍锁内完整校验。真实SQLite/PG及旧报告/B11回归37 passed，无skip，/tmp/aln-g02-backend.log；全量Ruff通过。
- TaskActions统一停止说明保留输入及已发布结果，cancel_requested正在停止、cancelled已停止；旧run未知不开放安全操作。分页选项/全部失败项/原基准+UUID请求键，双击互斥；409保持选择和版本，禁自动重基，新任务与父任务链接保留。Tasks操作链接、TaskDetail、BatchDetail、Floating入口及状态统一，取消不delete、不离开批次。
- useSSE绑定run_id/event_seq，拒旧run/旧sequence/正在停止之后迟到done及终态回退；GET轮询互斥、卸载清理、运输错误不改任务失败。检查远端代理时修复TaskDetail.id及retry_of_task_id未负号化造成与本地任务ID冲突的问题，器件item UUID保持原值，专门回归覆盖。
- 临时副本typecheck/npm test：203 passed/1既有skip；普通与static build通过，日志/tmp/aln-g02-{typecheck,unit,build,static-build,ruff}.log。单测包括停止未确认/已确认、双击一次、原版本/父任务链接、409保留、迟到done、旧run/序列、事件断开后GET确认cancelled。真实执行器停止竞争仍归B21，不宣称此处完成最终矩阵。git diff --check通过，stage空，无提交/push/部署/发布。G02 passed，38/43；下一G03。

## G03 删除影响、回收站与本地隐藏

- DeletionImpactDialog接Batches及BatchDetail，先读实际impact，失败/活动任务禁删除；原始输入/器件数/30天/清理时间及作用域明确。副本默认仅本地隐藏，独立远端radio只v3+remote_delete能力开放，另读UUID拥有端impact并核对固定来源/版本，改变禁删除不自动重基。Trash分页列软删项，restore成功刷新，410文件清理/409重名保留列表不覆盖；Settings列隐藏副本，可重新显示，仅本地偏好。static不读后端、不删恢复。
- 发现旧remote_access仍隐式按名称转发DELETE批次，修复为副本409 EXPLICIT_REMOTE_OPERATION_REQUIRED；显式UUID/v3删除/恢复及拥有端影响读取复用同一代理和服务器身份核验。普通副本impact保留本地作用域，不能被代理响应变成owned批次。客户端写能力补remote_delete与replica-preferences门禁。C08记录UUID影响读取适配；未增加服务或状态架构。
- 临时副本typecheck、npm test 208 passed/1既有skip、普通/静态build通过，/tmp/aln-g03-{typecheck,unit,build,static-build}.log。单测预览失败无删除、取消无写、活跃拒绝、软删刷新、v2禁远端、本地隐藏不调用远端、远端版本变化无写、410/409保留回收项及恢复成功。真实SQLite/PG生命周期/v3回归36 passed，无skip，验证UUIDimpact、副本旧DELETE拒绝、显式代理路径与身份、隐藏同步不物化、软删/恢复版本；/tmp/aln-g03-backend.log。全量Ruff/diff检查通过。首次类型/Ruff行宽失败已按当前代码修复。
- G03 passed，39/43；下一G04。真实跨进程远端删除验收仍归B21，不以本步HTTP/数据库测试替代；无提交/push/部署/发布。

## G04 工作区与完整筛选导出通过

- Explore导出数据入口明确当前工作区CSV/固定版本全部筛选源数据；保留既有工作区serializer，源XLSX复用独立ExportJob，不添加工作区XLSX。展示manifest、局部修改与排除、原匹配总数。未知来源禁完整源导出；UUID请求键+互斥双击。GET状态互斥轮询、success且manifest完整计数相符才下载；过期拒绝，cancel_requested不当已停、拒迟到运行状态回退，SOURCE_CHANGED保留项目且不重基。
- 静态完整导出在独立Worker/独立DuckDB内执行，抽取共享filtersToWhere与实际schema字段白名单，固定发布字节hash及SourceRef核对；先count、2000行块、200000行/100MiB硬限，失败不提供截断下载。CSV与manifest分别下载，XLSX能力关闭；Worker取消/失败/卸载终止，取消后迟到message不能重新成功。
- 真实native Chromium流程1 passed：工作区编辑999只进入当前CSV，完整CSV为3源行，实际下载XLSX由openpyxl迭代确认data_1四行含表头；/tmp/aln-g04-native-e2e.log。初轮先运行导出后上传导致空夹具，调整先准备数据；streaming XLSX max_row=None是读模式正常行为，改迭代实际行验证，未改服务产物。
- 实际静态bundle Chromium+独立Worker2 passed：完整8行/manifest、无API请求/XLSX关闭；实际200001行Arrow夹具经发布hash核验后拒绝，不截断下载，/tmp/aln-g04-static-e2e.log。发现单列null输出空行会被CSV读取器忽略，修复为引号空单元格后全部8行含缺值保留。所有Arrow/DB/CSV/XLSX/build在临时目录。
- 双数据库B17真实200001行CSV/ZIP及XLSX边界回归4 passed、19 deselected，无skip，/tmp/aln-g04-backend.log。临时前端typecheck、213 passed/1既有skip、普通与static build通过，/tmp/aln-g04-{typecheck,unit,build,static-build}.log；diff检查通过。修复F15质量区未知项不再同时声称范围内无异常。G04 passed，40/43；下一F16，真实总体门禁尚未完成。无stage/提交/push/部署/发布。


## F16 P4真实性能失败及架构调整待确认

- 实际Chromium/IndexedDB固定P4：50k首次保存188.9ms、恢复532.4ms、单格保存76.4ms；200k首次保存751.8ms、恢复2285.5ms、单格保存405.7ms。200k编辑保存最大主线程long task 113ms，超过100ms固定门限，性能测试1 passed/1 failed，不宣称F16通过。50k未观察到≥50ms的编辑long task，记录0只代表观察阈值下无记录，并非零阻塞。
- 已实施同架构优化：序列化复用不可变来源登记、去掉版本准备的重复structuredClone、在事务外每8个2000行块让出主线程；未改变数据库格式、2000行块、generation CAS或complete指针原子事务。200k原348ms阻塞降为113ms，但原生versions add/put分别108、112.3、112.3、107.9ms，外层分块无法拆分单次原生结构复制，门限仍失败。
- 临时副本typecheck及213前端单测通过（1既有skip）；真实项目回归12 passed，涵盖块复用、并发CAS、abort/quota、旧完整版本保护、损坏拒绝、自动保存、冲突、备份导入和markOpened只改打开元数据。证据/tmp/aln-f16-project-regression.log、/tmp/aln-f16-project-perf.log；性能摘要和原75路径复核位于/var/folders/fr/k3vpq27d5sd5zsy0ll16r_ym0000gn/T/aln-f16-evidence-f43u7kxl/，45 unchanged/30 changed/0 missing。未提交、stage、push、部署或发布。
- 建议增加项目存储Worker，保持C06格式、块大小、CAS及complete同事务提交；采用分块编码和可转移缓冲区，不能直接postMessage全量对象而再次阻塞主线程。该执行位置调整涉及架构，按用户“不自行更换架构”规则已提问，尚未收到确认，未实施Worker或修改已定契约。F16 blocked，B21/F17 pending，40/43通过；三端完整流程、峰值内存/报告性能和两执行器综合验收未完成。


## F16 Worker恢复与三端验收（2026-10-11）

- 用户批准后增量引入WorkerProjectStore/projectStorageWorker/projectWire，复用既有IndexedDbProjectStore事务、2000行块、generation CAS、complete指针原子提交；传输分块编码+transferable，autosave仅发送持久化状态，序列化在Worker。未增加数据库格式、依赖或框架。projectWire单测覆盖引用、非有限值和原型键；项目restore保留后端微秒generated_at字符串，避免转换毫秒后与来源metadata不符。
- typecheck与215单测通过，1既有skip仍非验收通过；普通与VITE_STATIC=1构建通过，全部在临时副本。实际ALN_RUN_PERF=1 npm run test:e2e：52 passed，2.2分钟，无skip（/tmp/aln-f16-all-e2e-worker-final.log）；真实Plotly hover观察超过autosave debounce后完整version未变化，另1 passed（/tmp/aln-f16-hover-save.log）。修正旧基准脚本缺失xFields/zField和旧分面断言；拖选测试改为按住鼠标等待真实scrollTop变化，替代固定150ms释放。
- 实际100k分桶图暴露Plotly拒绝customdata.__memberCount：保留旧成员映射、补memberCount并在hovertemplate读取合法键；先红后绿单测和真实100k图形复测，mapped=300000/emitted=150000，pan113.4fps/cache0.1ms。没有遗留调试探针。
- 三端真实query→编辑→项目保存刷新→完整同条件源比较→完整CSV→离线PNG HTML与真实capabilities/status通过。浏览器和Electron连接实际native server+SQLite（不冒称PG前端联调），静态实际DuckDB且无HTTP。真实Electron同profile进程SIGKILL后重启恢复2001行末值991、无undo/selection；这是崩溃恢复，不声称graceful close验收。实际多Worker CAS、quota/abort旧完整版本、块复用、JSON跨三端互导通过。
- 固定P4 50k：首次1195.5ms、恢复1703ms、单格保存961.9ms、command4.6ms；200k：首次5005.5ms、恢复7117.8ms、单格保存3984.9ms、command17.6ms。编辑long-task最大50ms通过≤100ms预算；50k记录0表示无≥50ms样本，不代表零阻塞。主线程IDB写0表示写入移至Worker，不代表写操作无成本。100ms采样实际Chromium进程合计RSS：50k baseline341328KiB/peak988848KiB；200k baseline346528KiB/peak1952016KiB。Worker消除原113ms主线程阻塞，但保存/恢复总耗时和内存仍高，不宣称全面提速；前端没有另设RSS预算。
- 产物/证据仅临时目录（/tmp/aln-f16-final-evidence-path指向根），含52项日志与Playwright附件；报告生成耗时见actual-report-generation附件。HEAD保持ab9bb94a6cc086920e1828c32ce2c75f96060263、stage空、diff检查通过。F16 passed，41/43；进入B21，整体尚未通过，无提交/push/部署/发布。


## B21 两执行器实测与必需材料阻塞（2026-10-11，检查收尾中）

- 新增tests/integration/test_platform_flow.py，临时API进程、PG唯一schema+专用Redis+真实Celery（eager=0）与native SQLite server+实际local executor。双方实际执行prepare→Barrier双HTTP commit→partial report→失败项重试→publish→query→独立export→版本冲突→软删恢复→running后取消确认；原task/report保留、重试同key同task、成功器件整数ID/UUID恢复不变。固定坏Touchstone合法有限但无共振，预检通过而提参失败；重试保留成功项故task成功/quality partial、失败项仍失败，不伪造坏输入恢复成功。
- /tmp/aln-b21-flow-v4.log：2 passed，16.65s，无skip；Celery日志有实际broker delivery/extract/compute/export，executor IDs持久证据；各executor acceptance.json保存在/tmp/aln-b21-flow-pytest-v4。另真实双连接Barrier发布/取消与两个native进程sync v3回归22 passed，15.26s（/tmp/aln-b21-race-sync.log）。
- 实际PG API启动发现app/db.py connect_args.options覆盖DATABASE_URL search_path，使隔离schema失效；修复为保留URL options并追加client_encoding。两真实执行器复测通过。没有使用用户数据库，PG基址是专用aln_test。
- NN旧夹具file_path=dummy.zip却在files/TEST001创建文件，与B05持久路径契约不符；改为实际临时目录，原红1 failed/1 passed→4 passed。批次重命名旧测试仍patch已删除get_settings且要求移动文件，与B05契约不符；去掉废弃seam并验证持久路径/原文件不变，原11 setup错误→11 passed。证据/tmp/aln-b21-{nn-red,nn-green,rename-red,rename-green}.log；均为测试夹具修正，无业务口径放宽。
- 新增test_platform_performance.py，实际200001完整CSV逐ID核对，50ms采样实际执行器进程RSS。SQLite baseline556285952B/peak559316992B，增量3031040B，导出1.148s；PG baseline127107072B/peak131792896B，增量4685824B，1.081s。均≤baseline+256MiB，CSV产物和manifest在临时根。SQLite实际1M guarded publish为79.825s/3034 SQL调用/1000 executemany，百万行数量与result_revision=1验证；PG百万行与全量后端检查仍在运行，尚未宣称通过。
- 必需旧命令uv run pytest -m integration tests/test_real_pipeline.py已实际执行（/tmp/aln-b21-old-pipeline.log），1 failed：FileNotFoundError mapping_ELB003.xlsx，发生在TRUNCATE和HTTP前。mapping_ELB003.xlsx与T8901P.01.zip均是指向仓库客户提供的材料的断链，项目父目录按准确文件名检索亦未找到，真实客户算法样本未验收。没有把合成样本替换为客户材料或降低≥20器件/≥5 SSE progress门限。
- 按用户“缺少必需环境停下”规则，B21 blocked，F17 pending，41/43完成。恢复需补齐客户原始两个材料，再在专用PG/Redis/Celery/API8001完成旧管线；不得以已通过新流程替代。正在收齐此前已启动的检查，不推进依赖B21的交接完成状态。无stage/提交/push/部署/发布。

### B21全量结果与夹具复测补充

- 专用PG schema的完整uv/pytest检查：854 passed、17 skipped、4 failed、11 errors，425.10s（/tmp/aln-b21-full-v3.log）。11 errors是已上述修复的重命名废弃patch；另外2 managed extraction旧断言把未发布staging当活动目录，改为核验run.stage_dir层级为.runs/sourceUUID/runUUID/leaseUUID并保持原活动目录、旧文件与输入不变；2 deembed旧测试用无共振常量DUT且读显示名目录、要求公开错误含内部文本，改为有效同频段共振DUT真去嵌+提参+publish、读取持久batch.file_path，缺校准仍检查实际异常OPEN/SHORT、持久安全错误码PROCESSING_FAILED且原ZIP保留。不是放宽零成功结果或失败发布门禁。
- 基于同一pytest lastfailed清单、重新创建独立PG schema，uv/pytest --lf：15 passed，3.08s（/tmp/aln-b21-full-lf.log），此前全部新增失败已修复复测；未重复未变的大容量检查。报告保留原完整命令与复测两份结果，不称原完整命令一次全绿。17 skip未作为通过，包括缺客户样本及需要显式integration的新/旧真实流程；对应新流程另以-m integration实际2 passed，两类数据库竞争/同步实际22 passed。
- 全量Ruff与git diff --check通过。原75受保护路径45摘要未变/30本任务叠加路径/0缺失，HEAD和空stage保持；审计及实际Celery/流程附件在/tmp/aln-b21-evidence-path指定临时根。旧客户材料门禁仍blocked，PG百万行性能仍待收尾，F17不提前通过。


### B21停止位置与最终保护检查

- 因旧客户材料门禁缺失按停止规则停在B21；收尾时PG百万行publish仍处于bulk_insert_mappings/psycopg.executemany，最后数据库观察xact 589.82s仍active且wait_event_type=null。没有实际commit和million-row结果验证，PG 1M性能记未完成；其事务秒数门限在当前计划未给数值，不判为预算通过。主动中断隔离性能测试，退出130/KeyboardInterrupt，完整命令不是2 passed；SQLite已完成1M的JSON/SQL证据和两类200001导出JSON均保留。
- 本次自建native前端服务54216已关闭；性能fixture的SQLite API PID81773、PG Celery PID82298均已停止，专用Redis/API无剩余进程，PG唯一性能schema aln_flow_4d6e141c991f4a25ba2da1f89203975f已不存在，未提交PG百万行事务已撤回。没有停止原先已存在的55432测试PG服务。源码外临时产物保留供审阅。
- 证据根/tmp/aln-b21-evidence-path：实际Celery日志、sqlite/postgresql-flow.json、两类export-performance.json、sqlite-publication-performance.json、完整/--lf日志、中断性能日志、保护审计。F16证据根/tmp/aln-f16-final-evidence-path保留完整52 E2E日志、三端/报告附件与P4指标。
- 最终Ruff和git diff --check通过，HEAD保持ab9bb94a6cc086920e1828c32ce2c75f96060263，stage空；75原路径均存在，45摘要未变/30已有叠加改动。没有提交、push、部署、发布或重置工作树。41/43步骤passed；B21 blocked、F17 pending，不宣称总体完成。
- 恢复入口：补齐backend/tests/fixtures两个链接对应的原始客户文件T8901P.01.zip与mapping_ELB003.xlsx（仓库客户提供的材料目录）；先复核原材料身份，在隔离PG/Redis/Celery/API8001运行旧真实管线，再完成PG百万行性能测量/必要诊断，全部门禁通过后执行F17。合成算法样本不能代替客户回归材料。

### B21继续请求后的阻塞复核（2026-10-12）

- 复核Git：HEAD仍为ab9bb94a6cc086920e1828c32ce2c75f96060263、stage为空、git diff --check通过。原75受保护路径45摘要未变/30叠加改动/0缺失；本轮新增当前工作树文件摘要、tracked二进制diff及状态快照，仅保存在临时目录/var/folders/fr/k3vpq27d5sd5zsy0ll16r_ym0000gn/T/aln-b21-recheck-zodms18e（/tmp/aln-b21-recheck-evidence-path指向该目录）。
- 两个fixture仍为断链，目标客户提供的材料/T8901P.01.zip与客户提供的材料/mapping_ELB003.xlsx均不存在；按准确名称检索项目父目录也未找到。已请求用户提供两个原始文件所在的本机目录路径，不用合成数据代替。没有重复运行确定缺材料的旧管线或未完成的PG百万行测试。
- 41/43保持passed；B21 blocked、F17 pending。PG百万行发布性能仍未完成，与原材料管线共同构成剩余验收。按既定停止规则等待必需材料；本轮仅补充执行日志，不修改业务代码，不stage、提交、push、部署或发布。

### B21客户表格核对与验收范围待统一（2026-10-12）

- 用户提供/Users/jingbozuo/Desktop/mapping_ELB003.xlsx及/Users/jingbozuo/Desktop/#21_results_node.xlsx，并确认原ZIP因体积过大已删除。只读检查两份原文件，检查前后SHA256不变；没有修改原文件、fixture断链或用户数据库。临时证据/tmp/aln-b21-customer-sheets-path指向/var/folders/fr/k3vpq27d5sd5zsy0ll16r_ym0000gn/T/aln-b21-customer-sheets-_qy84xfd，含本轮开始时Git diff/status、源摘要、字段和数据质量检查。
- 映射Sheet1为749条唯一Mark，无重复。结果Sheet1为7553条唯一original_filename、29列；Sheet2为204条，文件名全部包含在Sheet1内，不能当新增204条直接拼接。主要字段含端口、坐标、结构参数、fs/fp、Qs/Qp、BodeQ及k2eff；未发现数值非有限值。Sheet1有7349条Type空值，应保留未知；7481条按文件名器件Mark关联映射，Description完全一致，另72条site命名没有对应Mark，应保留未映射，不编造结构身份或参数。
- 表格是既有标量结果，不包含原始S参数曲线，不能用于重跑ZIP解包、提参、去嵌或证明原客户算法回归通过。现有B21要求旧test_real_pipeline.py读取T8901P.01.zip，因此仍blocked；本轮材料检查不是旧管线验收通过，进度保持41/43。
- 提议等待用户确认的具体范围修订：客户材料门禁改为使用真实结果表作只读快照回归，覆盖行数/端口/映射/未知字段保留、查询、完整导出与报告；Sheet1为完整主表，Sheet2为已标注子集，禁止重复计数。保留已实测的PG+Celery/Redis与native SQLite原始处理流程回归；原客户ZIP算法/SSE旧管线明确列为未验证，并从本次必须完成的门禁移出。PG百万行发布测量仍须完成，原子性和其余契约保持。尚未得到范围确认，未修改B21契约或推进F17。

### B21最终验收收尾（2026-10-12，passed）

- 用户确认推荐材料范围，C01验收范围/B21/契约复查已更新。原客户ZIP算法/SSE回归仍未验证，移出本次必需门禁；结果表没有原曲线/算法条件，未补造证据。所有输入只读、产物在临时目录，未修改客户文件或断链fixture。
- tests/integration/test_customer_snapshot.py在真实PG+Celery/Redis与native SQLite各执行一次：原XLSX映射上传接口entry_count=749；主表7553条原文件名唯一，附表204条不重复合并；7481条映射Description一致、72条site未映射、7349条Type空值保留。查询结果表映射的26个Device字段及测试器件UUID逐值核对、两端口3787/3766计数核对、7553行完整CSV逐值核对；数据只播种隔离测试库已有标量，不把此过程声称为原始提参或新增生产Excel导入入口。/tmp/aln-b21-customer-header-red.log名字为探测标签，实际2 passed/9.27s，无失败；较早v3为2 passed/10.09s。输入路径用显式ALN_CUSTOMER_*环境变量，不提交客户数据；SourceRef只属于新建测试库，不伪装原客户UUID。
- 真实浏览器完整快照经Worker保存、刷新恢复逐值核验全部原字段（含空Type），qs统计7553有效值/均值独立核对、完整工作区CSV、实际Plotly PNG离线HTML与A4 PDF通过；/tmp/aln-b21-customer-e2e-final.log为2 passed/12.8s，无skip。报告保留unverified来源、未知算法/频段和未包含曲线说明；无后端模型metadata时显示未知，proxy连接拒绝是该明确缺失的记录，不声称模型指标已验证。客户报告图改为按真实端口分组的Qs分布，避免连续频率类别轴标签密集。原F16三端52项验收保留，不将此次browser-only新增回归冒称三端重测。
- PG性能根因一：逐行INSERT修订触发器反复修改单行epoch/revision，2000行三次0.079–0.085s、8000行0.843s，仅在隔离schema禁用该触发器后8000行0.107s。真实EXPLAIN回归先失败Calls=8000，后合并为每条INSERT一次增量，精确epoch/revision仍+8000；COPY多批次、空INSERT、回滚、冻结迁移升降级通过。新增a41d0c7e52ab迁移保持单链；发布PG分块多值INSERT，保留None默认值语义/连续字段组，未改变租约检查、身份、分块上限或同事务提交。SQLite实现保持原路径。
- PG性能根因二：NOT IN在百万暂存行统计信息下选择Materialize SubPlan，反复扫描；首次本轮million命令主动中断，1 passed/KeyboardInterrupt，不能写成两类通过。真实100k、ANALYZE、64kB work_mem回归先出现5s statement_timeout；改为相关NOT EXISTS反连接并显式保持旧NULL身份行为后通过，走暂存联合主键索引。没有提高work_mem/超时或拆分事务掩盖问题。临时探针与执行计划只在/tmp/aln-b21-resume-path；未留下生产调试日志。
- 最终tests/services/test_postgres_bulk_revision.py、test_result_publish.py、test_data_revision.py、test_platform_schema_upgrade.py、test_native_replication_v3.py、test_platform_flow.py显式真实环境回归：45 passed/1既有Alembic warning，34.31s，/tmp/aln-b21-final-regression.log。包含两类实际处理执行器、Barrier竞争、同步双进程；未用eager/mock替代。最终完整后端pytest：873 passed、19 skipped、16 warnings，151.70s，/tmp/aln-b21-full-final-v2.log；全部新增失败已修复，skip仍非通过。Ruff通过，frontend typecheck通过。
- 最终实际200001完整CSV及1M guarded publication命令：2 passed/241.05s，无skip（/tmp/aln-b21-perf-final2.log）。SQLite export baseline556711936B/peak559579136B，增量2867200B，1.241s；PG baseline127303680B/peak131940352B，增量4636672B，1.199s，均小于+256MiB预算，50ms真实执行器RSS采样。SQLite发布65.496s/3034 SQL/1000 executemany；PG94.053s/3037 SQL/0 executemany，均实际commit后核对1000000行、result_revision=1。当前计划未规定发布秒数预算，不把94s宣称延迟SLO通过；事务仍较长，记录为后续优化风险。未改变原子发布契约。
- 最终HEAD仍ab9bb94a6cc086920e1828c32ce2c75f96060263、stage空、diff检查通过；原75资产45摘要未变/30职责增量/0缺失。最终证据根/tmp/aln-b21-resume-path指向/var/folders/fr/k3vpq27d5sd5zsy0ll16r_ym0000gn/T/aln-b21-resume-dipdkrzx；性能JSON在/tmp/aln-b21-perf-final2，两份原文件和受保护既有部署/打包资产未改变。B21 passed，42/43；下一F17最终交接，不构成提交/push/部署/发布授权。

### F17最终交接（2026-10-12，passed）

- 前置F16/B21 passed，新增docs/plans/platform-optimization-frontend-acceptance.md，列出全部步骤对应实现、实际命令/结果、三端能力、客户材料范围调整、模型/原曲线未知、PG长事务/Worker内存代价、站点数据清除风险、JSON备份恢复步骤和实际截图路径。T1/T2/T4/T6/T8/T9本次必需依赖已实现并验证；原客户ZIP、缺失模型验证证据及实际Windows/LAN部署未被冒称通过。入口与契约首页的“尚未实现”规划时态已更新为当前验收记录。
- 实际查看B21证据根/customer-report/customer-report.png，标题、来源未验证提示、7553有效值统计、原pf计数、未知算法/模型说明和S11/S22两组Qs图可读；同目录保留HTML、A4 PDF和完整工作区CSV。所有数据、图片、构建、探针、SQL计划、报告与测试日志仅临时目录。
- 基线对照：原75路径45摘要未变/30增量/0缺失；12份原始受保护前端副本均与B00 SHA一致，已把当前相对它们的职责增量patch保存于证据根/baseline-diffs。769份当前非忽略文件摘要、最终tracked diff和Git状态归档，工作树不清理；不能将原用户改动算成本任务或整文件stage。
- 最终Ruff、git diff --check通过；HEAD保持ab9bb94a6cc086920e1828c32ce2c75f96060263，stage为空。独立进程清点无本轮pytest/API/Celery/Vite/Playwright残留，6份实际executor PID均已退出；所有本轮aln_flow/aln_full/aln_revision_test/aln_migration_test隔离PG schema已不存在，原55432服务保留。审计文件owned-process-check.json、schema-cleanup.json、f17-protection.json在B21证据根。
- 43/43步骤passed，按照用户确认的材料调整范围完成。873后端检查/19 skip、45真实专项、两类客户快照、两类200001导出/1M发布、原三端52 E2E及客户浏览器2报告回归证据均保留，skip和未验证项不转成pass。没有stage、提交、push、部署、发布或更新生产/个人数据。
