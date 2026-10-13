# 平台优化最终验收与交接

日期：2026-10-12。43个步骤按入口顺序完成，范围包含用户确认的B21客户材料调整。完整逐步文件、命令、失败及修复记录见[执行日志](platform-optimization-execution-log.md)，字段与状态以[共用契约](platform-optimization-luna-contracts.md)为准。没有提交、push、部署、发布或修改用户数据库。

原T8901P.01.zip已删除，原客户ZIP算法/SSE回归明确未验证；用户确认将其移出本次必需门禁，以真实客户结果快照回归替代本次客户材料验收。两类执行器真实处理流程与百万行发布另有实际证据，结果表不能证明原始曲线算法正确。

## 交付对应关系

| 步骤 | 本任务主要增量 | 实际检查 |
| --- | --- | --- |
| B00/F01 | platform_cases.py、前端基线记录及仓库外SHA清单 | 原75资产保护，原始脏树边界记录；基线问题已分类 |
| B01–B03 | schemas/source.py、processing.py、analysis.py、result_manifest.py；schema_upgrades.py、source_identity.py、data_revision.py、迁移 | 契约、SQLite新库/v4升级/拒绝降级、真实PG迁移、COPY身份、业务修订和空更新/回滚；最终单链head=a41d0c7e52ab |
| B04–B05 | processing_run.py、run_execution.py、device_ingest.py、UUID文件目录 | 独立task/run、租约、投递失败确认例外、批量事务及旧路径保留；真实执行器另见B21 |
| B06–B08 | upload_precheck.py、upload_draft_service.py、processing_report.py与API | prepare/check/commit、组恢复、幂等、文件/器件分计数、全部问题与质量状态 |
| B09–B11 | result_publish.py、processing_cancel.py、processing_retry.py、retry-options API | staging不可见、租约/revision检查、同事务发布、取消竞争、迟到写者、保留旧结果和重试原基准 |
| B12–B14 | batch_scope.py、batch_lifecycle.py、artifact_lifecycle.py、replication_v3.py | 有效批次范围、30天软删规则、独立Process域、文件租约、显式删除/恢复、双进程同步与冲突 |
| B15–B18 | query_spec.py、query_manifest.py、export_job_service.py、export_writer.py、comparison_service.py | 未知总数口径、固定来源流式导出、执行器终态保护、条件预审查与两来源计算时再次核验 |
| B19–B20 | capabilities.py、sync_status.py、model_metadata.py及API | 实际模式能力、同步持久状态、模型已存在产物读取；缺少证据显示未知 |
| F02–F07 | features/analysis/model/provenance.ts、project/*、result/*、workspace.tsx、Explore.tsx | 行身份和编辑/排除记录；JSON迁移/校验、2000行块、complete指针、CAS、恢复/备份、范围提示 |
| F08–F10 | features/analysis/comparison/*、只读conditions接口 | 固定两来源、条件差异、完整载入、分布/差值/wafer和选中联动；不能用筛选改处理频段 |
| F11–F12 | features/analysis/report/* | 冻结已保存版本，实际PNG、自包含离线HTML、A4横向PDF、模型未知说明和恶意备注转义 |
| F13–F15 | Settings.tsx、Dashboard.tsx、sync/*、capabilities.ts、ProjectMenu.tsx | 三端能力、单一同步协调器、最近项目/问题入口、命名项目与异常定位 |
| G01–G04 | upload/*、tasks/*、lifecycle/*、export/*、Trash.tsx | 多文件预检查与警告确认；停止确认/重试勾选；本地移除/远端删除分离；工作区/全筛选导出分离 |
| F16 | WorkerProjectStore.ts、projectStorageWorker.ts、projectWire.ts及真实E2E | 保留格式、块、CAS和原子性；三端52 E2E、hover不保存专项、50k/200k实测 |
| B21 | tests/integration/test_platform_flow.py、test_customer_snapshot.py、test_platform_performance.py；tests/services/test_postgres_bulk_revision.py | 真PG/Celery/Redis、native SQLite，两数据库客户查询/CSV、实际完整报告、200001导出RSS及1M提交核验 |
| F17 | 本文及最终保护审计、执行日志 | 门禁/范围/证据/备份路径对照；原75路径均存在、HEAD不变、stage空、diff检查通过 |

原生复制/远端曲线访问、原Electron启动与版本文案、既有同步面板、deploy/native-v2及打包资料在B00前已有，不将其全部归为本任务交付。Git全量diff含原用户改动且不包含untracked内容，不能整体stage或当成本任务补丁直接提交。

## 最终检查证据

后端命令在backend目录，使用uv、独立临时PG schema、临时DATA_ROOT/cache/basetemp。PG测试基址为专用127.0.0.1:55432/aln_test；fixture自建Redis/API/Celery并在finally清理，未使用eager模式。前端命令在临时副本执行，所有构建、报告、CSV、JSON、PDF及截图均在仓库外。

| 实际命令或检查 | 结果 | 临时证据 |
| --- | --- | --- |
| uv run ruff check . | 通过 | B21证据根的Ruff日志；F17再次检查通过 |
| uv run pytest -q（临时PG schema启动器） | 873 passed、19 skipped、16 warnings，151.70s | /tmp/aln-b21-full-final-v2.log；启动器在B21证据根/full-check.py |
| pytest显式运行clock/publish/revision/migration/native-v3/真实platform-flow | 45 passed，34.31s，无skip | /tmp/aln-b21-final-regression.log；完整文件清单在B21日志 |
| uv run pytest -m integration tests/integration/test_customer_snapshot.py | 两类数据库2 passed，映射接口749、结果7553及完整CSV逐值核验 | /tmp/aln-b21-customer-header-red.log；文件名是历史探测标签，实际没有失败 |
| uv run pytest -m integration tests/integration/test_platform_performance.py | 2 passed，241.05s；两类200001导出与1M真实提交 | /tmp/aln-b21-perf-final2.log及两类performance.json |
| npm run typecheck / npm test / 普通及VITE_STATIC=1构建 | F16：typecheck、215单测通过/1既有skip、两构建通过；F17前typecheck再次通过 | F16执行日志；/tmp/aln-b21-frontend-typecheck.log |
| ALN_RUN_PERF=1 npm run test:e2e | F16完整52 passed，无skip；hover不新增完整版本另1 passed | F16证据根/e2e-52-passed.log及hover-no-save.log |
| ALN_CUSTOMER_SNAPSHOT_JSON=... npm run test:e2e -- customerSnapshot.spec.ts report.spec.ts | 实际浏览器2 passed，12.8s，无skip；完整保存恢复、全部原字段、CSV、PNG HTML/PDF | /tmp/aln-b21-customer-e2e-final.log及B21证据根/customer-report |
| git diff --check / HEAD / stage / 75资产SHA / 唯一迁移head | 通过；原45摘要未变/30有职责增量/0缺失；HEAD仍ab9bb94a6cc086920e1828c32ce2c75f96060263；a41d0c7e52ab为唯一head | B21证据根/f17-protection.json、baseline-diffs及最终Git快照 |

19个skip不计为通过；本次必需的真实PG、Redis、Celery、SQLite、Chromium、Electron、DuckDB均另有实际运行证据。缺失客户ZIP以及既有可选客户样本回归不声称通过。

重新运行客户回归需显式ALN_CUSTOMER_MAPPING_FILE、ALN_CUSTOMER_RESULTS_FILE和ALN_CUSTOMER_SNAPSHOT_OUT，最后一个必须是临时输出目录；浏览器测试读取该目录生成的customer-snapshot.json。不把原始表或转换数据提交为仓库fixture。

## 三端实际能力

| 前端 | 本次实际验收 | 限制与范围 |
| --- | --- | --- |
| 浏览器 | F16连接真实native SQLite源端，query→编辑→完整项目恢复→固定来源比较→完整CSV→PNG报告/离线HTML；B21新增真实客户快照回归 | 浏览器连PG未单独重测；PG API/任务执行器由B21独立真实验证 |
| Electron/macOS | F16真实Electron同profile强制结束进程后重启恢复2001行及编辑末值；真实源端查询/比较/完整导出、三端JSON互转 | 验证的是崩溃恢复，未声称graceful close或实际Windows安装/LAN部署通过 |
| 无后端静态版/DuckDB | F16实际发布快照查询、项目保存、快照比较、受限完整CSV、PNG报告；无HTTP请求 | 无上传/任务/回收站/远端删除/双向同步；完整CSV≤200000行且≤100MiB，XLSX关闭；200001行实际拒绝 |

两类服务器执行器分别是实际PG+Celery/Redis和native SQLite本地执行器；独立任务/发布/查询/导出/停止恢复流程均实际运行。静态限制由能力与容量检查共同执行，没有用disabled按钮代替服务器功能交付。

## 性能和未验证边界

| 测量 | 实际结果 | 判断 |
| --- | --- | --- |
| Worker项目50k | 首次1195.5ms，恢复1703ms，单格保存961.9ms；RSS peak988848KiB | 记录总耗时；无≥50ms编辑long-task样本不等于零阻塞 |
| Worker项目200k | 首次5005.5ms，恢复7117.8ms，单格保存3984.9ms；RSS peak1952016KiB | 编辑主线程最大50ms，满足≤100ms；总耗时与约1.86GiB峰值仍高，不宣称全面提速 |
| SQLite 200001 CSV | baseline556711936B/peak559579136B，增量2867200B，1.241s | 满足baseline+256MiB预算，50ms执行器RSS采样，完整逐ID校验 |
| PG 200001 CSV | baseline127303680B/peak131940352B，增量4636672B，1.199s | 同上，真实Celery进程 |
| SQLite 1M publish | 65.496s，3034 SQL调用、1000 executemany | commit后核验1000000行和result_revision=1 |
| PG 1M publish | 94.053s，3037 SQL调用、0 executemany | 同上；修订增量按行精确保留，INSERT聚合时钟，清理使用索引反连接 |

原NOT IN百万行Materialize及逐行时钟更新失败/中断证据保留；修复没有增加work_mem、延长回归超时、放宽租约或拆分发布事务。计划没有规定发布秒数SLO，94秒仅是实测完成，较长事务仍是后续优化风险。性能结果是本机测量，包含本轮其他隔离检查的系统负载，不外推生产并发吞吐。

原客户结果表7553条保留29个原字段：Type空值7349条、72条site命名未映射；附表204条不拼接重复。报告来源仍unverified，处理算法/频段未知，没有原曲线。此次报告模型metadata服务不可用时如实显示未知；没有训练模型或编造验证指标。

## 备份和恢复

分析页打开“项目”，等待“已保存”，用“导出JSON”保存.aln-project.json到用户选择位置；在另一端用“导入JSON”验证后恢复。固定快照只读，编辑需“另存为”。模板只保存分析配置，不能替代含表格数据的完整项目备份。

命名项目仍存在当前站点/profile的IndexedDB内。清除站点数据、换域名/profile或移除应用数据可能删除它；在这些操作前先导出JSON。多窗口冲突使用“重载其他窗口版本”或“另存为新项目”，不得静默覆盖。会话存储失败时可以导出会话JSON，但不能把下载成功说成已在浏览器保存。

恢复后会话undo/redo/selection清空，原始曲线和凭据不在备份内；曲线继续依赖源端已有原文件及访问权限。旧PageState不是完整项目备份来源。全筛选源导出固定来源版本，工作区CSV包含本地编辑，两者用途及范围保持区分。

## 依赖和保护交接

T1契约/身份/迁移、T2预检查/验收、T4回收站/同步、T6查询/导出、T8能力/协调器/工作台、T9报告/模型说明在本次约定范围内已具备实际实现和检查，不存在以空接口或disabled UI顶替的本次必需依赖。仍未就绪的是原客户ZIP回归材料、模型缺失的验证证据及实际Windows/办公室LAN发布验证；前者经用户移出本次门禁，后两者没有被冒称为本次已验证能力。未来找回ZIP需在隔离服务恢复旧回归，不直接运行其TRUNCATE脚本指向用户库。

原75个用户资产全部存在，45摘要不变、30文件由计划职责增量叠加。12份受保护前端原始副本与初始SHA一致，可在B21证据根/baseline-diffs查看本任务相对这些副本的增量。其他原始混合脏树边界参见frontend-baseline与git-audit记录，不能将所有git diff归为本轮或整文件提交。

证据均为临时目录，可能被系统清除；当前实际路径：

- F16：/var/folders/fr/k3vpq27d5sd5zsy0ll16r_ym0000gn/T/aln-f16-final-evidence-i4sn6juv（/tmp/aln-f16-final-evidence-path）。
- B21/F17：/var/folders/fr/k3vpq27d5sd5zsy0ll16r_ym0000gn/T/aln-b21-resume-dipdkrzx（/tmp/aln-b21-resume-path）。
- 实际报告截图：B21证据根/customer-report/customer-report.png；同目录含实际离线HTML、PDF和工作区CSV，已查看PNG确认端口分组图与来源/未知说明可读。

本轮自建API/Celery/Redis执行器已停止，所有aln_flow/aln_full/aln_revision_test/aln_migration_test隔离schema已清理；原55432测试PG进程保留。生产库、个人全局配置、原部署/打包文件均未清理或更新。当前交付是可审阅工作树和验收文档，不是上线或发布。
