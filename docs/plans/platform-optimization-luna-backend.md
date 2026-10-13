# Luna 后端执行步骤

日期：2026-10-09。状态：未执行。必读同目录 contracts 文件；这里的新增文件/测试名是实施目标，不是声称已经存在。仓库根目录为 `/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master`；Python 命令工作目录均为 backend/。

每步只做列出的变更，先阅读对应函数再编辑；完成该步检查才更新执行记录。新增接口统一在 main.py 注册 `/api` 前缀，models 在 models/__init__.py 注册。每步结束记录修改文件、命令、结果和未完成项。下文的步骤不能靠写占位函数、mock 返回成功或 skip 测试完成。

## B00 冻结执行基线（T0）

前置：无。本计划核查时 HEAD 为 `ab9bb94a6cc086920e1828c32ce2c75f96060263`，工作树包含在途本机服务器/副本改动。

1. 读取根 AGENTS.md、技术计划、contracts。执行 `git status --short`、`git rev-parse HEAD`、`git diff --name-only`；记录到 docs/plans/platform-optimization-execution-log.md 的“执行基线”。新增未跟踪文件也按既有资产对待。
2. 对本步执行时所有已修改业务源码记录 SHA256 到本地 `/tmp/aln-plan-baseline-<uuid>/`，只记录路径和摘要，不复制 .env/凭据/数据库。每步提交前检查无与任务无关的变动。HEAD 变化只要求重读受影响文件，不自动 reset/stash/清理。
3. 执行 `uv run ruff check .`、`uv run pytest`；前端 `npm run typecheck`、`npm test`。缺依赖时先 uv sync/npm ci（后者仅当 node_modules 缺失且锁文件一致）；不能把安装导致的锁文件变化顺手提交。
4. 错误逐项记既有失败/环境缺失，包含命令和测试名；不在本项目优化中修复不相关旧失败。缺 PostgreSQL/Redis 不伪造真实集成通过。
5. 新增 `tests/fixtures/platform_cases.py` 生成临时测试数据，所有大 ZIP/数据库/导出产物用 tmp_path，不提交：2个DUT S2P、1个DUT S1P、2个校准文件、1个坏Touchstone、1个忽略文件；另有重复成员和缺校准包；调用现有算法 fixture 生成有效格式，不用虚假随机频谱替代算法正确性验证。

完成：基线记录包含桌面 SQLite、原生 SQLite 服务器、PG+Celery、静态版四种运行组合；区分“需要检查”与“已检查”。

## B01 共享契约与状态规则（T1）

前置 B00。新增 schemas/source.py、processing.py、analysis.py、result_manifest.py、capabilities.py；类型字段严格按C01/C02/C05/C09，同时新增frontend/src/types/platformContracts.ts和固定契约JSON样例供F02消费。新增 services/task_state.py：`can_transition(old,new)`、`transition_task(session,task_id,run_id,lease_token,expected_status,new_status)`，返回changed/noop/conflict。

复用 schemas/query.py 的 filters/MetricSpec，但 QuerySpec 不包含 limit；新增字段均保留旧响应兼容。新增 `tests/services/test_task_state.py` 验证所有允许转换、终态幂等、迟到 running 不覆盖 cancelled、错误 token 无变化；新增 `tests/api/test_contract_examples.py` 验证固定JSON样例与空/未知值。不从文案推断状态。

检查：`uv run pytest tests/services/test_task_state.py tests/api/test_contract_examples.py`，`uv run ruff check app/schemas app/services/task_state.py tests/services/test_task_state.py tests/api/test_contract_examples.py`。完成：统一规则被进度、发布、取消复用，不保留第二份冲突规则。

## B02 新表和双路径升级（T1）

前置 B01。新增 models/processing.py、source.py、lifecycle.py、export_job.py，以及必要的 upload_drafts/sync_operations 模型；按 C 字段建表。批次、设备与任务增量字段和 CHECK 修改分为一条连续 Alembic 迁移链，先执行 `uv run alembic heads` 固定真实 down_revision，不能填写猜测的 migration ID。

桌面当前 SCHEMA_VERSION=4。新增共享 `schema_upgrades.py` 和 `_migrate_v4_to_v5`：inspect 列/表后增量升级，SQLite CHECK 通过 batch table rebuild，复制全部原列和数据/索引/外键，不能漏 cancelled_at、kind、synced_at；新库直接最终结构，已升级库启动幂等；更高版本继续拒绝降级。原生 SQLite server 走同一路径。迁移提交后才记 `_aln_schema_version=5`。

新增 tests/models/test_platform_schema_upgrade.py，使用真正v4 schema SQL快照（从当前模型/迁移验证后固定）而非新模型create_all当旧库；分别验证新库、v4含记录、重复启动、迁移中途异常回滚、更高版本拒绝；PG migration用专用临时schema测试，绝不对用户运行库试迁移。

检查：`uv run pytest tests/models/test_platform_schema_upgrade.py`。完成：旧 tasks/devices计数、字段和FK关系保持，SQLite PRAGMA foreign_key_check为空。多 Alembic head必须先解决兼容关系，不能自动覆盖。

## B03 稳定身份、版本与缓存时钟（T1）

前置 B02。新增 services/source_identity.py：`ensure_origin_identity`、`register_source_input`、`resolve_device_identity`、`backfill_source_refs`。复用 replica_service.identity 的同一身份；init_replication 扩展PG元数据创建，PG触发器和SQLite触发器分别实现，不能把INSERT OR IGNORE送给PG。

扩展snapshot_service与scripts/export_web_snapshot.py的发布manifest，附origin/source UUID与修订，DuckDB行保留源器件UUID；旧静态快照没有这些字段时明确unverified。先按C01分批回填，副本UUID来自源响应；旧副本缺UUID标unverified并按源同步回填，不以本地整数id冒认。检查Device COPY列定义加入source_device_key；复用原 natural key仅匹配已有SourceInput。current_relpath跟随实际产物路径；虚拟file_tree移动/rename保留实际路径，真实路径替换在B09发布事务中同步映射。

触发器覆盖devices/batches/mapping_entries/mappings及定义查询口径的process数据。按C01的业务字段新旧值比较替换当前无差别UPDATE触发器；task_id/active_run_id/synced_at等操作字段不推进revision，source_inputs注册在run捕获base前完成。进度/心跳/staging不推进epoch。query缓存、聚合缓存、batch_stats与devices曲线缓存加入revision/epoch；测试开始run不会立即导致自身版本冲突。

新增 tests/services/test_source_identity.py、test_data_revision.py：同名异目录、双端口、虚拟移动/重命名后原路径仍可读且UUID/member_key/current_relpath不变、真实路径映射更新复用UUID、重复登记、ORM/bulk/COPY、来源修订、staging不改epoch；缓存读入旧结果后更新数据须立即失效。SQLite与PG都用真实数据库路径；PG特有验证标integration。B09补验真实路径切换与发布同事务，失败/取消保留旧路径。

检查：`uv run pytest tests/services/test_source_identity.py tests/services/test_data_revision.py tests/api/test_query_cache.py tests/api/test_devices_cache.py`。完成：本地重处理保留UUID，跨端下载不生成新源UUID，批量更新也推进版本。

## B04 处理租约和独立任务记录（T1/T3）

前置 B03。新增 services/processing_run.py：`create_run`、`claim_run`、`heartbeat_run`、`assert_run_writable`、`finish_run`。batch lock为PG FOR UPDATE、SQLite fresh Session BEGIN IMMEDIATE；同批次 active_run_id判冲突。

改 api/batches.py `_start_reprocess`：每次新建UploadTask，保留原任务，Batch.task_id更新新任务；create_batch_and_dispatch也创建run。dispatch_batch_task/dispatch_reprocess_task、extract结果dict、LocalTask参数都传 run_id+lease_token，不能只靠task_id。Celery和本地进程都在服务领取租约。Celery链在extract与compute之间采用阶段交接token：extract结束记录handoff状态，compute在锁中claim下一阶段新token，extract旧token失效；重复compute claim不成功，不能在每个重复投递上生成可覆盖旧租约的token。

投递发生在持久pending之后；仅确认未入队、未领取且仍queued的投递失败按C02例外同步标task/run failed并释放active_run_id，保留输入；投递结果不确定保留pending与投递错误，不能覆盖已领取租约；heartbeat与主计算用不同Session。新增 tests/services/test_processing_run.py验证双提交、重复投递、任务记录保留、旧token、broker失败；tests/workers/test_run_dispatch.py验证两执行器携带同参数。

检查：`uv run pytest tests/services/test_processing_run.py tests/workers/test_run_dispatch.py tests/api/test_reprocess.py`。完成：每次尝试可追溯；无并行活动写入者。

## B05 清理写入服务的事务边界（T3）

前置 B04。改 device_ingest.py `copy_insert_devices`、`bulk_insert_devices`与compute_service.py写入函数为 `commit=False` 默认，由调用者明确提交；兼容现有直接调用处逐个补commit，避免悄悄丢数据。COPY异常使用savepoint并rollback该savepoint后再ORM fallback，不能在已失败的PG事务继续写。

file_tree_service.py `build_file_tree_from_disk`新增 `commit=False`、可传预扫描manifest；事务内不扫描磁盘。batch_files_dir改为解析实际 Batch.file_path，而非拼 batch_no；检查api/files.py、raw_download.py、rename_batch_service.py所有路径拼接，新managed目录通过UUID保持稳定，rename只更新显示名，旧目录兼容读取。

新增 tests/services/test_ingest_transactions.py、test_managed_batch_paths.py：写入后rollback无结果，COPY回退成功，同事务file tree可回滚，重命名不会移动另一个run目录。

检查：`uv run pytest tests/services/test_ingest_transactions.py tests/services/test_managed_batch_paths.py tests/api/test_file_tree.py tests/api/test_rename_batch.py`。完成：发布依赖函数内部无隐藏commit，旧路径仍可读。

## B06 预检查服务（T2）

前置 B05。新增 services/upload_precheck.py：`validate_upload_options`、`inspect_archive`、`inspect_table`、`build_precheck_report`、`calculate_check_digest`。复用extract_batch安全解压和pipeline/calibration已有匹配函数，提取为可复用服务后保留旧入口。

按C03处理：格式/容量检查在接收前后；包内重复成员、路径逃逸、解压容量、mapping未命中、坏Touchstone、缺校准件、频段非有限/起止颠倒/实际区间不匹配。S1P是否真的去嵌不能仅由deembed勾选设true，报告明确来源；表格_rows_from_xlsx的失败列表不能再忽略。

深检查是独立precheck job写草稿和质量报告，在计算前完成；不可直接跑extract_resonator_params生成指标。check_digest涵盖input、options、mapping和关联目标revision。

新增 tests/services/test_upload_precheck.py覆盖正常、空包、坏文件、重复路径、双端口、缺校准、mapping修改、表格partial。检查 `uv run pytest tests/services/test_upload_precheck.py`。完成：同输入/选项产生同检查摘要，无Device活动行写入。

## B07 两阶段上传API与一次消费（T2）

前置 B06。改api/upload.py、upload_service.py；新增workers/precheck.py、dispatch_precheck_job和LocalPrecheckTask；实现C03的prepare/get/recheck/commit/delete draft。

上传request_key绑定输入摘要：相同key相同输入返回同draft，摘要不同409；先写临时文件，完整落盘后才注册draft，接收中断清理临时输入。按C03存group_id并提供组状态GET接口。commit使用draft+batch锁，唯一消费；commit后dispatch失败返回已有task failed而不是重新创建批次。警告code集合必须覆盖报告且check_digest一致；耗时网络投递不占SQLite写锁。

现有POST /uploads、目录watch和sync push保留兼容，调用同一服务。避免在参数表导入同步路径遗漏ProcessingRun/summary。

新增 tests/api/test_upload_drafts.py：一次上传、重复prepare/commit、摘要冲突、过期、改mapping重检、警告确认、fatal拒绝、投递失败。检查 `uv run pytest tests/api/test_upload_drafts.py tests/api/test_upload_target.py tests/services/test_upload_service.py`。

完成：提交/重试不会重复上传或重复入库；预检查中不占用批次活动结果目录。

## B08 持久记录与验收摘要（T2）

前置 B07。新增 services/processing_report.py：`record_item_result`、`record_issue`、`summarize_run`。计算子进程只返回结构化结果，父worker按块写item，重试写同一item幂等；并行降级不得重复计数。

改 compute_batch `_extract_single/_extract_parallel`、extract任务、reprocess任务、table导入；记录file分类/端口处理，失败不只存 failures[:5]。GET /tasks/{id}/report返回summary与分页issues，GET /tasks/{id}/issues.csv流式全部问题；TaskDetail新增run_id、quality_status、summary，未知旧记录标unknown。

summary无需频繁COUNT全表，阶段末/终态汇总持久化，实时返回durable最新摘要。新增 tests/services/test_processing_report.py、tests/api/test_processing_report.py，验证混合包计数平衡、S2P双端口、全部问题导出、重启报告可读、并行fallback无重复。

检查：`uv run pytest tests/services/test_processing_report.py tests/api/test_processing_report.py tests/workers/test_process_batch.py`。完成：成功、部分成功、失败三种结果有可核对原因。

## B09 暂存计算与一次发布（T3）

前置 B08。新增 services/result_publish.py：`stage_device_rows`、`validate_staged_result`、`publish_run`。先改 recompute，再改 extract/compute/redeembed/remap；搜所有delete(Device)、bulk_update、batch.file_path赋值和KEEP_RAW_ZIP，确保活动数据仅在publish变更。

发布固定C04：fresh session锁batch，确认run/lease/revision/cancel/mapping，白名单批量更新，preserve Device.id/source UUID；预构建FileNode manifest一同发布；result_revision+1、active_run_id清空、run/task成功同一commit。重复调用已成功run直接返回发布结果，不再加revision。

重算partial保留失败器件旧值且报告partial；全替换处理存在失败就不发布旧批次；首次partial可发布且UI明确，零成功失败。commit后publisher只发送已持久状态，不能再无条件done覆盖取消；统计MV刷新在PG执行且失败记录可修复。大发布事务按批SQL执行但单commit，性能预算超限不能擅自改成部分commit。

新增 tests/services/test_result_publish.py：阶段后崩溃、取消发布竞争、重复发布、旧token、mapping变更、recompute局部失败、file tree回滚、原Device id保留。检查 `uv run pytest tests/services/test_result_publish.py tests/workers/test_reprocess_batch.py tests/workers/test_pipeline_batch.py`（本轮已核实这两个既有测试文件存在）。

完成：任何发布前失败，旧指标、目录、FileNode一致不变；新成功结果整体可查询。

## B10 安全取消与恢复巡检（T3）

前置 B09。改api/tasks.cancel_task：写cancel_requested并触发执行器停止，移除delete_batch_and_files；pending任务执行器确认已移除才cancelled。running通过检查点/本地子进程join确认，绝不能join超时仍宣称已停。

local_worker_loop先合作停止，超过CANCEL_GRACE可terminate自身管理进程，join确认失败时保持cancel_requested且错误码STOP_NOT_CONFIRMED；server对extract和compute各stage task_id记录控制，不能只revoke最后chain id。使用协作检查点和租约屏障，无法确认进程停止时不删除staging；过期租约隔离活动写入，由巡检确认/标failed，保留取消状态历史。

ProgressPublisher改条件更新，SSE初始snapshot包含stage/run/summary，cancel_requested继续流，不以error事件提前结束；confirmed cancelled才终止。Redis故障客户端仍能GET状态。recover_interrupted_tasks包括cancel_requested，并区分server仍有活worker不能因API重启统一failed；桌面自有进程结束后可判中断。

新增 tests/api/test_cancel_safety.py、tests/workers/test_cancel_races.py，更新test_cancel_task旧“删文件”断言为保留；真正双连接SQLite和PG竞争验证，不只mock publisher。

检查：`uv run pytest tests/api/test_cancel_task.py tests/api/test_cancel_safety.py tests/workers/test_cancel_races.py`。完成：取消确认准确、原始输入保留、迟到写者无法发布。

## B11 失败项重试（T3）

前置 B10。新增 services/processing_retry.py `create_retry_run`并接C04 retry端点；新run保存原run引用和选中item列表，源UUID+配置摘要+idempotency request_key唯一绑定。

活batch锁内确认只选failed/cancelled可重试器件，不能选已成功或别run item；缺文件/校准返回问题，摘要/版本变化409；成功base与重试结果在B09发布服务合并，不能先删旧成功行。失败草稿未发布数据仍可重试。每次retry生成新task，原报告不可改。

新增 tests/services/test_processing_retry.py：两客户端同时retry、重复key、源变更、缺校准、混合成功与失败数量、旧报告保留。检查 `uv run pytest tests/services/test_processing_retry.py`。完成：第二次retry请求无重复器件，无丢失原成功数据。

## B12 有效数据作用域与回收站（T4）

前置 B11。新增 services/batch_scope.py `active_batch_clause`、`get_active_batch`，services/batch_lifecycle.py `deletion_impact`、`soft_delete`、`restore`。按C08路由，所有批次关联读取入口包括query/aggregate/distinct/stats、Device曲线、文件下载、旧export、snapshots过滤Batch.deleted_at；GET trash是显式例外。2026-10-10用户确认：现有三类Process工艺表独立于Batch，不按批次deleted_at过滤，不猜测名称关联；验收删除/恢复不会改变独立工艺记录。

API软删除batch前持锁核验没有pending/running/cancel_requested，操作记录含来源和影响，不含秘密。保留TaskReport可查；restore保持UUID并推进revision，名字被占用时409给出重命名方案，不自动覆盖另批次。

新增tests/api/test_batch_trash.py、test_active_batch_scope.py验证列表/计数/聚合/直接id曲线/下载不可读，恢复一致，重复soft-delete幂等，活动任务删除409。检查 `uv run pytest tests/api/test_batch_trash.py tests/api/test_active_batch_scope.py tests/api/test_batch_mv.py`。

## B13 清理租约和可恢复物理清理（T4）

前置 B12。新增CleanupJob与ArtifactLease（run/download/export用途，过期时间），services/artifact_lifecycle.py `acquire_lease`、`renew_lease`、`release_lease`、`purge_due`。下载FileResponse结束和中断均释放；即使释放失败也能到期。

原delete_batch_and_files改为内部purge步骤，只用于确认soft-delete且保留期满、无活租约。先写purge intent+路径白名单，删除操作校验managed root及源origin；完成文件删除再提交已清理标记与tombstone。DB已写意图而IO失败可重试；不捕获异常后报告清理完成。

服务器Celery beat与桌面启动/后台维护调用同服务；使用现有队列模块注册路径，PG服务与native SQLite均覆盖。source身份和tombstone不得因cascade消失。

新增tests/services/test_artifact_lifecycle.py：权限错误、断电中断步骤、重复purge、路径外逃、租约、恢复/清理竞争。检查 `uv run pytest tests/services/test_artifact_lifecycle.py`。完成：删除物理文件与数据库状态可恢复收敛。

## B14 同步v3和显式冲突（T4/T8）

前置 B13。新增api/replication_v3.py、schemas/replication_v3.py，service实现复用replica_service序列化，不再复制一套表格导入；稳定来源UUID和完整catalog遵循C08。

实现C08的UUID来源resolve及replica-preferences接口。v2 catalog保留tombstone占位ready=false，v3显式删除/恢复；synchronize先验证complete/server_id/protocol，局部/失败catalog不删本地。LocalReplicaPreference阻止副本回流，远端delete单独操作、expected_revision保护。删除/恢复不能对本地owned同名批次产生作用。

删除当前synchronize末尾自动healed table-target revision逻辑；409保存原base，产物仍留待用户复核。关联指标提交在同一锁/事务核查revision，再写receipt；重复key且不同digest409。

新增tests/services/test_replication_v3.py和tests/test_native_replication_v3.py：server版本更新、离线删除恢复、分页/部分目录、旧v2、hidden副本、身份变更、表格冲突不覆盖、PG身份/receipt。检查 `uv run pytest tests/services/test_replication_v3.py tests/services/test_replication_v2.py`；native两真实SQLite后端流程单独运行验证，PG/Redis集成缺环境记未验证。

## B15 查询manifest与统一口径（T6）

前置 B03；同时B09发布已完成才能宣称verified。新增services/query_spec.py抽取field/filter/order验证，目前export_query依赖api.query私有函数需调整为service共用，api保持兼容包装，避免循环import。

query_devices/aggregate/distinct加入active_scope/source refs/epoch；在同一读事务获得来源和行，SQL排序追加id。skip_count截断manifest total未知，采样rate只表示返回集合系统步进采样比例，不描述总体概率。exact count与NULL有效值明确。

新增 tests/api/test_query_manifest.py、tests/services/test_query_spec.py，fixtures覆盖AND/OR、仅Batch字段、无结果、截断、采样、更新缓存、源revision变化；旧query_safety/sampling继续通过。检查 `uv run pytest tests/api/test_query_manifest.py tests/services/test_query_spec.py tests/api/test_query_safety.py tests/api/test_query_sampling.py`。

完成：旧字段不破坏，新客户端无法把lower bound当精确总数。

## B16 导出任务与执行器（T6）

前置 B15、B10。新增services/export_job_service.py、api/export_jobs.py、workers/export_job.py；实现C07 Create/Get/Cancel/Download，路由注册固定/export/jobs在任何参数匹配前。ExportJob包含request_key/digest/query_spec/sources/status/lease_token/progress/rows/artifact_manifest/error/created/finished/expires。

dispatch_export_job服务器Celery、桌面LocalExportTask；local_worker用明确typed handler map支持Processing/Precheck/Export，保持原任务兼容，不把exports挂到UploadTask。启动恢复遵循2026-10-10用户确认C07：只处理确认旧写者停止的export，接管新租约后pending→running→failed、running→failed、cancel_requested→cancelled；活跃/停止未知保留原状态，不自动重跑，完整成功文件保留。nonce只绑定request_key同内容，不重复任务。

新增tests/api/test_export_jobs.py、tests/workers/test_export_dispatch.py。检查 `uv run pytest tests/api/test_export_jobs.py tests/workers/test_export_dispatch.py`。完成：pending/running/cancel_requested/success/failed/cancelled真实可查询且下载门禁有效。

## B17 快照一致的流式导出（T6）

前置 B16。新增services/export_writer.py、query_snapshot.py，实现C07只读snapshot/2000行批次。PG事务隔离在首条查询前设REPEATABLE READ，SQLite用显式BEGIN，cursor/连接在finally关闭；heartbeat另一Session。完工独立Session比较epoch，无数据目录新增遗漏；变更保守失败、文件不公开。

CSV用csv writer；XLSX write_only分sheet。去掉新路径limit裁剪，count超EXPORT_MAX_ROWS明确失败；运行中字节预算、取消和输出IO失败清理.part。CSV/Excel公式注入按字符串单元格安全策略处理并记录是否转义，数值负值仍数值。manifest JSON不得含NaN或绝对路径。

新增tests/services/test_full_export.py：200001行实际导出并核对id集合；为了sheet边界性能，单元测试用可配置小上限且至少一条真实Excel容量边界集成验收（临时数据）；修改epoch、新增匹配batch、文件异常、取消、配额、未知字段；不能用mock rows长度代替产物读取。

检查 `uv run pytest tests/services/test_full_export.py tests/api/test_export_jobs.py tests/api/test_export.py`。完成：CSV和XLSX行数等于manifest，来源变更无混版成功产物。

## B18 对比统计与规则（T7）

前置B15。新增schemas/comparison.py、services/comparison_service.py、api/comparison.py：POST /api/analysis/compare，请求 `{comparison:AnalysisSpec.comparison,quality_rule:QualityRuleV1|null}`，服务器仅接受scope=source。返回各指标valid/missing n、mean/median/p25/p75/min/max、分组差值B-A、条件差异、manifest，以及绘图cohorts(rows及_source_ref)。源模式group_fields限定eg/fl/ag/s_param_port；两cohort必要数值列各≤200000行，超过明确失败，不调用图表limit冒充完整读取。

统计库使用现有numpy，分位数固定linear，ddof=1的std仅n≥2；增量SQL取必要列，每组超过COMPARISON_MAX_ROWS=200000明确失败，不采样冒充完整统计。容差abs≤1e-9或rel≤1e-8；规则unknown与fail分开。条件差异未确认返回422 CONDITIONS_DIFFER；单位未知不可比较。静态用同一JSON夹具核对，不为静态调用后端。

新增tests/services/test_comparison_service.py、tests/api/test_comparison.py，固定数据[1,2,3,null]中位2、有效n3、missing1、自比较0；一个批次空值全空、不同单位拒绝、pf与规则不同。检查 `uv run pytest tests/services/test_comparison_service.py tests/api/test_comparison.py`。

## B19 能力与持久同步状态（T8）

前置 B14/B17。api/system.py增加GET capabilities，按真实初始化/协议/数据库能力返回，不因IS_DESKTOP自动允许所有功能。native SQLite server mode=server但database=sqlite；允许remote_delete要求源端v3握手成功且权限有效。

新增services/sync_status.py及sync_operations：每target独立记录receipt和源revision；desktop_sync/push_table/web_sync改为持久同步过程。GET /api/batches/{name}/sync-status返回server/web两状态。readback核对目标源UUID/revision/digest，匹配才confirmed；Git仓写成功但网页未发布pending，readback不可用unknown或failed，不显示确认成功。

冲突返回可复核动作，不自动重基；系统状态不返回token。新增tests/api/test_capabilities.py、tests/services/test_sync_status.py：PG/server/native/desktop，目标发布延迟、409、假receipt、断网、重启。检查 `uv run pytest tests/api/test_capabilities.py tests/services/test_sync_status.py tests/services/test_desktop_sync_service.py`。

## B20 模型说明元数据（T9）

前置 B01。定位backend/scripts/export_surrogate_onnx.py及现有surrogate状态/前端静态metadata，新增schemas/model_metadata.py，导出产物附model digest/输入范围/来源/验证数据摘要/已有指标。数据来源读取当前模型训练配置与已存在评估产物；无来源为null+unknown，不重新训练或填估计值。

GET surrogate/status保持旧字段并新增metadata，静态发布模型同步生成同schema JSON。新增tests/api/test_model_metadata.py验证缺失评估、已知metadata、超范围标识、旧产物兼容；模型不可用不阻塞一般分析项目。

检查 `uv run pytest tests/api/test_model_metadata.py tests/api/test_surrogate_api.py`。完成：实测/仿真来源、适用范围和指标可解释且无虚构数据。

## B21 后端综合门禁（T10）

前置本次交付批所有B步骤及对应F步骤。执行 `uv run ruff check .`、`uv run pytest`；与B00基线逐项对照，新失败必须修复。新增tests/integration/test_platform_flow.py，以专用tmp数据根启动真实API/执行器和数据库，不使用用户DATA_ROOT。

运行 `uv run pytest -m integration tests/integration/test_platform_flow.py`：PG+Celery+Redis与native SQLite server各一次，覆盖prepare→commit→partial report→retry→cancel→publish→query→export→soft-delete→restore→同步冲突。真实双连接验证publish/cancel与两同时commit，事件竞争夹具用Barrier而非固定sleep。

2026-10-12用户确认材料范围调整：原始T8901P.01.zip已删除，旧 `uv run pytest -m integration tests/test_real_pipeline.py` 的原客户ZIP算法/SSE回归明确保留“未验证”，移出本次必需门禁，不将结果表或合成曲线声称为该回归通过。保留上述PG/Celery/Redis与native SQLite真实处理流程门禁。

使用用户提供的mapping_ELB003.xlsx和#21_results_node.xlsx新增真实结果快照回归：只读原文件，数据产物仅临时目录；映射749条，结果Sheet1共7553条，Sheet2的204条为主表子集，不重复合并。核验端口、映射命中、空Type及未映射site记录保留；以已有结果快照验证真实查询、完整导出和PNG离线HTML报告。不补造算法/频段/原始输入摘要或跨端身份，结果表不支持原曲线计算。输入路径通过显式测试环境变量提供，不把客户数据提交到仓库。任何其他环境缺失仍记“未验证”，不把skip当通过；PG百万行发布测量仍为必需门禁。

性能门禁使用T0固定设备和数据：保存50k/200k行、200001行导出、1M发布SQL；比较峰值RSS和耗时。固定首版预算：导出峰值RSS≤baseline进程RSS+256MiB，前端编辑期间单次主线程阻塞≤100ms（如测不到则未验证），不允许全量重新JSON.stringify作为每次编辑步骤。发布长事务超预算时记录失败并交给规划者修订，不自行改原子性。

完成：执行日志给出命令、数量、实际产物验收和未覆盖环境。文档不自动构成上线授权。

## 路径核对与实施停止规则

- 测试路径可能在实施时被用户改动：先 `rg --files` 核对；本计划新测试名明确可创建，既有测试名不存在时记录更名并定位同一职责，不能跳过检查。
- 当前 remap 有kind契约但不等于完整处理入口；若发现mapping修改直接在api/mappings或process写Device，必须接入B09发布服务后才开放remap能力。
- 2026-10-10用户确认：可明确诊断的代码或测试夹具失败自主修复并复测后继续；契约冲突、UUID无法唯一回填、必需验收环境缺失或用户在途改动被覆盖时停在对应步骤给出证据。禁止擅自换架构或抹掉问题。
- 文档引用相对路径均相对于仓库root（除Python命令cwd）；不提交临时数据库、输入数据、导出、日志、模型和dist产物。

### B18补充：C05a只读条件接口（2026-10-10用户确认，F08前执行）

增量api/comparison.py、services/comparison_service.py及对应测试。复用_cohort/_conditions和source_snapshot；响应按C05a，不调用statistics/_summarize，不写数据，来源变化整体拒绝。SQLite/PG与HTTP测试覆盖无计算、未知/篡改证据、旧版本、端口结构差异具体值。仅修订此契约缺口，B18原计算端点保持末端再验证，预审查不替代计算时校验。

### G02只读重试选项补充（2026-10-10用户确认）

GET /api/tasks/{id}/retry-options?offset=0&limit=100只读返回{task_id,run_id,expected_source_revision:number|null,retry_of_task_id:number|null,items,next_offset:number|null}。分页上限500、稳定item ID排序；items={id,unit_kind,source_member,port,status,error_code}。基准取原run的published_source_revision，否则base_source_revision，绝不替换为当前批次版本。复用B11失败/取消器件或表格行筛选，额外要求已有source_device_key或同批次member/port SourceInput证据；只对已结束run提供项。旧run/基准未知为空项，禁止安全重试。只返回相对成员路径；不读原文件、不创建任务、不改版本。POST retry继续独立校验输入、映射、配置和当前版本；409保留选项、不自动换基准。retry_of_task_id由原父run的task_id取得。
