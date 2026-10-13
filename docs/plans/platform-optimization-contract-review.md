# 平台优化实施契约待确认项

## B12：独立工艺表的软删作用域

状态：2026-10-10用户已确认推荐方案；B12恢复执行，契约与后端分册已同步修订。

证据：

- `platform-optimization-luna-backend.md:135`要求“所有读取入口包括query/aggregate/distinct/stats/Process、Device曲线、文件下载、旧export、snapshots过滤deleted_at”。
- `backend/app/models/process.py:3`明确“独立数据域（暂不关联 devices/batches）”；ProcessIcpEtchRun、ProcessMoDepRun、ProcessAlnDepRun都没有batch_id、source_batch_id或批次关系。
- `backend/app/api/process.py:99`及`:110`直接查询独立工艺表；当前没有可核验的工艺记录→批次映射，不能用run_no/名称猜测关系。

推荐方案：维持现有架构。B12仅过滤具有Batch关系的数据：批次列表/详情/计数、Device查询/聚合/曲线/文件、批次统计、导出和快照；独立Process工艺表维持原有读取作用域，业务修改仍按C01推进data_epoch。增加验收：删除/恢复某批次不能改变独立工艺表记录数量与内容。

确认后对B12相关句子的具体修订：

> 所有批次关联读取入口包括query/aggregate/distinct/stats、Device曲线、文件下载、旧export、snapshots过滤Batch.deleted_at；GET trash是显式例外。现有三类Process工艺表是独立数据域，没有Batch关联，不按批次deleted_at过滤，不通过名称猜测关联。测试核验批次软删/恢复不会改变独立工艺记录。

另一方案：要求工艺记录跟随批次软删。需先补齐来源关联契约、历史记录映射规则、未知/多对多关系处理、数据库迁移与同步/导出影响，再修订B12。此方案增加架构，当前不得自行实施。

停止来源：用户明确要求“遇到契约冲突或新增失败停在对应步骤给出证据”，以及执行入口的契约优先级与后端分册的实施停止规则；不是新增审批流程。

## B16 启动恢复状态冲突（2026-10-10，待用户决策）

- contracts C07第159行要求ExportJob沿用C02状态；C02第45行只有pending→running→success/failed、pending/running→cancel_requested→cancelled及确认未入队的processing投递失败例外。backend B16第175行却要求启动时“未结束export显式failed”，包含pending与cancel_requested，无法同时满足。
- C02禁止API重启推断活Celery进程已停止。无租约/存活证据强制失败还会允许旧写者继续完成文件，违背迟到发布屏障。
- 推荐统一文字：启动恢复仅处理已确认旧写者停止的导出任务，接管新租约后pending→running→failed，running→failed，cancel_requested→cancelled；活跃或停止状态未知保留原状态及可定位说明，不自动重跑，完整success文件保留。服务器恢复须基于worker停止或租约过期证据，不以API启动为依据。
- 等待用户确认上述规则或另给ExportJob明确的恢复例外；当前未写B16业务代码，不把时间当批准。

- 用户已确认上述保留C02方案，C07/B16同步修订，冲突已解除。

## G02：重试选项与版本读取缺口（2026-10-10，待确认）

G01真实上传流程通过后发现G02的报告勾选/expected_source_revision/retry_of UI没有对应读字段。现有report只返回summary/issues，详情没有base_source_revision或父任务；B11服务要求原任务基准、器件或表格行失败/取消项及源身份。不能由当前revision、错误文案或问题item_id猜测。证据与完整建议见执行日志G02段落。

建议补只读GET /api/tasks/{id}/retry-options（分页100，上限500），公开原任务基准、可验证可重试项及父任务ID；复用B11筛选，旧记录未知禁重试，继续由POST核验输入及版本。用户确认后同步C04/B11/G02契约，再编码/SQLite与PG验收。目前未更改契约或G02业务代码。停止源于用户规定的契约冲突停止规则。

G02用户已确认推荐方案；只读契约和双数据库/前端门禁已通过，冲突解除。


## F16：200k项目存储阻塞与Worker执行位置（待用户确认）

实际P4 200k编辑保存主线程113ms，超过固定100ms；版本元数据原生IndexedDB add/put阻塞108–112.3ms。已消除重复复制并在事务外分块让出执行权，仍不能拆开原生调用。证据见执行日志F16和/tmp/aln-f16-project-perf.log。

建议用户批准增加存储Worker执行位置，保持C06数据库格式、2000行块、generation CAS和complete指针同事务提交；主线程以分块编码与可转移缓冲区传输，不传全量对象。确认后更新契约，实施并复测双容量性能、真实IDB原子性、多窗口冲突、三端重启恢复。确认前保留现架构并将F16记为blocked。需要确认源于用户明确禁止自行更换架构，不是自动审批拒绝。

F16用户已确认存储Worker方案；同步契约并实施，保留原格式与事务。每次编辑的项目序列化也在Worker中执行，主线程只捕获不可变registry/spec/样式/备注等明确字段，通过分批缓冲区传输，不传会话selection/history。最终门禁仍待实测，不因确认而自动标记通过。

## B21：客户原始ZIP删除后的材料范围（2026-10-12，已确认）

用户提供桌面mapping_ELB003.xlsx和#21_results_node.xlsx，确认原T8901P.01.zip已删除，并确认按推荐范围调整继续。两份文件只读检查及前后摘要一致；结果主表7553行、附表204行文件名均在主表中，禁止重复计数。原ZIP客户算法/SSE回归保留未验证，移出本次必需门禁；两执行器真实处理流程、结果快照查询/完整导出/报告以及PG百万行发布测量仍为必需。共用契约和B21已同步，此决定不是伪造原曲线或生产发布授权。

F16已完成存储Worker实测与三端验收，见执行日志2026-10-11段落；该门禁现已passed，原113ms失败记录保留为历史证据。
