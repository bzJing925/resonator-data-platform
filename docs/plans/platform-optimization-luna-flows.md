# Luna 操作流程界面补充任务书

日期2026-10-09，状态未执行。共用contracts为唯一字段规范。与前端F任务书使用同一V/S/E命令约定；执行顺序按入口文件。

## G01 多文件预检查与入库验收（T2）

前置B07/B08/B19、F13。新增frontend/src/upload/UploadGroup.tsx、useUploadDrafts.ts、PrecheckReport.tsx、IngestionReport.tsx、uploadDrafts.test.tsx；增量Upload.tsx、UploadProgressContext、FloatingUploadProgress、TaskDetail.tsx、api/endpoints.ts。

步骤：

1. 文件选择生成group_id及每文件request_key，点击“检查数据”走C03 prepare。校验零字节、同name/size/lastModified重复选择、频段有限且start<end；浏览器重复判定仅提示，实际重复以后端摘要判定。
2. 逐文件展示上传/检查/待确认/排队/计算/成功/部分成功/失败；GET group恢复，groupId放URL参数和轻量偏好，不保存File对象或token。刷新时未上传的File须重选并明确提示。
3. ready展示实际器件/端口数和警告确认；点击启动入库逐draft commit，不重发文件。expired显示复查/重选；PRECHECK_STALE走recheck；fatal禁止提交。
4. 全部文件完成保留验收单和每批次入口，不自动跳第一个成功任务。Floating显示组统计并链接详情。
5. summary消费B08分离的file/device计数；task report和问题CSV可下载。每1s轮询且请求互斥，卸载终止；事件中断GET状态，不根据错误文案把任务标failed。

测试夹具：同组2成功1失败、S2P双端口、不重复上传、commit双击、expired/recheck、partial非全部成功、组刷新。检查 `npm test -- uploadDrafts.test.tsx` 和V；新增e2e/analysis/upload-flow.spec.ts为真实后端流程，static只测入口不可用。完成：所有文件状态和问题可定位。

## G02 停止任务与失败项重试（T3）

前置B10/B11、G01。新增frontend/src/tasks/taskStatus.ts、TaskActions.tsx、taskActions.test.tsx；增量useSSE.ts、Tasks.tsx、TaskDetail.tsx、BatchDetail.tsx、FloatingUploadProgress。

1. 各页统一“停止处理”，说明保留输入及已发布结果；cancel_requested显示正在停止，仅cancelled显示已停止。cancel请求返回不能删除列表或直接显示完成。
2. SSE接收run_id/event_seq，旧run/旧sequence丢弃，确认终态后关闭；Progress snapshot/GET无seq时以当前run状态为准。旧版事件只读兼容，不宣称新版安全停止能力。
3. 重试只列report可重试项，勾选失败项或全部失败项，按C04提交request_key+expected revision；双击用同key。
4. 409保留选项并说明输入/版本变化，要求整批新处理或重新核对；不能自动换expected revision覆盖。原报告可查，新task显示retry_of链接。

测试cancel→cancel_requested→cancelled、STOP_NOT_CONFIRMED、迟到done、旧run、重复retry、409；检查 `npm test -- taskActions.test.tsx`、V，真实竞争由B21验证。完成：UI与实际终止确认一致，停止不调用delete。

## G03 删除影响、回收站与副本隐藏（T4）

前置B12/B13/B14、F13/F14。新增frontend/src/pages/Trash.tsx、lifecycle/DeletionImpactDialog.tsx、lifecycleActions.test.tsx；增量Batches.tsx、BatchDetail.tsx、App.tsx、Settings.tsx、api/endpoints.ts。

1. 删除先GET impact，展示器件数、原始文件、作用域、保留期、活动任务。server_owned副本提供移除本地副本/删除服务器批次两独立操作；remote delete仅v3+权限允许并由用户选择。
2. 本地隐藏用 `POST /api/replica-preferences` JSON `{source_ref,hidden:true}`，重新显示hidden:false；B14实现此路由。禁止沿用当前副本删除即转远端删除路径。
3. DELETE成功刷新active列表与统计，Trash单独列出可恢复项。410说明文件已清理；409重名冲突不自动覆盖。任何失败保留原数据并显示错误。
4. 同步刷新hidden副本不回流；static无删/恢复操作。

测试预览失败不delete、用户取消、active task拒绝、软删恢复、重名、本地隐藏无远端调用、v2不开放remote-delete。检查 `npm test -- lifecycleActions.test.tsx`、V/S。完成：本地/远端范围准确。

## G04 工作区与全筛选导出（T6）

前置B16/B17、F06/F07/F13。新增frontend/src/export/ExportDialog.tsx、ExportJobProgress.tsx、useExportJob.ts、exportJob.test.tsx；增量Explore.tsx、api/endpoints.ts、api/static/index.ts。

1. 固定入口当前工作区（含本地修改）/全部筛选结果（源数据），显示manifest及差异。前者保留CSV serializer；工作区XLSX首版不提供，原Excel按钮归全筛选并说明数据来源。
2. server/desktop走C07 job，轮询状态，success才下载，显示行数与来源；cancel_requested不当已停，错误/过期可定位。
3. static新增api/static/fullExportWorker.ts，Worker内独立DuckDB，使用共享filtersToWhere与字段白名单。先count及预算；≤200000行/100MiB才2000行迭代编码CSV并生成manifest。两个文件分别下载，不新增压缩依赖；static full_export_xlsx=false。
4. 超预算明确拒绝、不截断成功；DuckDB失败/内存不足停止并释放Worker，不调用后端。request_key双击幂等；expected_sources来自query manifest，unknown禁止声称完整源导出，工作区导出仍可用。
5. SOURCE_CHANGED保留分析并要求重新查询，不自动換版本下载。metadata不含token/绝对路径。

测试200001行server完整/static拒绝、局部编辑仅影响工作区、取消、源改变、过期、双击、unverified。检查 `npm test -- exportJob.test.tsx`、V/S，新增e2e/analysis/export-flow.spec.ts核对实际CSV/XLSX。完成：文件范围、行数和界面声明一致。

### G02只读重试选项补充（2026-10-10用户确认）

GET /api/tasks/{id}/retry-options?offset=0&limit=100只读返回{task_id,run_id,expected_source_revision:number|null,retry_of_task_id:number|null,items,next_offset:number|null}。分页上限500、稳定item ID排序；items={id,unit_kind,source_member,port,status,error_code}。基准取原run的published_source_revision，否则base_source_revision，绝不替换为当前批次版本。复用B11失败/取消器件或表格行筛选，额外要求已有source_device_key或同批次member/port SourceInput证据；只对已结束run提供项。旧run/基准未知为空项，禁止安全重试。只返回相对成员路径；不读原文件、不创建任务、不改版本。POST retry继续独立校验输入、映射、配置和当前版本；409保留选项、不自动换基准。retry_of_task_id由原父run的task_id取得。
