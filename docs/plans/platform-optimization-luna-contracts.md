# Luna 执行契约与已确定的设计

日期：2026-10-09编制，2026-10-12验收更新。与 `platform-optimization-luna-backend.md`、`platform-optimization-luna-frontend.md` 配套。本文件是本次新增实现的规范；类型、表、接口与验收状态见执行日志及最终交接文档。

## C01 身份与三种版本

```ts
type SourceRef = {
  origin_id: string;
  source_batch_id: string; // 永久 UUID，不能使用本地整数 ID
  source_device_key: string | null; // 永久 UUID，批次级引用时为 null
  source_revision: number | null; // 来源方同步版本；旧数据无法证明时为 null
};
```

- 数据库实例 `origin_id` 复用 SyncIdentity 的持久身份；PostgreSQL 同样持久初始化，不使用机器名、启动时间或临时 UUID。
- Batch 增加 `origin_id`、`source_batch_id`；Device 增加 `source_device_key`，表内仍保留整数主键，既有 API 不更换主键。
- 新批次 UUID4；输入登记表 `source_inputs` 首次建立“原始包内成员原始相对路径+端口→器件 UUID”的映射，器件 UUID4。路径用 POSIX 格式，保留大小写，禁止 `..`、绝对路径、大小写折叠和 basename 匹配。路径只是匹配辅助，永久身份是 UUID。
- 同一 S2P 的 S11/S22 是两个器件身份。直接 S1P 是一个。ZIP 中同名同路径重复成员视为不可唯一识别的错误，不覆盖。相同内容、不同目录不能自动合并。
- `SourceInput` 另存 input_digest、当前解析产物路径、原始文件名、端口和建立时间；current_relpath记录实际解析产物的POSIX相对路径。虚拟文件树移动、显示名称修改、排序只改变展示结构，不修改current_relpath、Device.s_param_path、member_key或UUID。真实产物路径变化时保持UUID与原始匹配键，在发布事务中同步切换current_relpath、Device.s_param_path与文件树manifest；staging不提前覆盖活动路径，失败/取消保留旧路径。重算复用源登记；重新解压只在原始成员键与内容摘要可核验时继承 UUID，歧义显式报告。
- 旧 Device 分批回填 UUID；同一旧记录获得一个持久身份，不回头声称它与其他客户端旧记录等同。服务器下发 UUID，副本保留源 UUID，不重新生成。
- 参数表独立导入没有曲线，仍有器件 UUID；重试同一草稿复用身份。关联服务器批次优先用 UUID；旧表格自然键只在目录/文件名/端口唯一时允许匹配，否则 409。
- 三种版本：dataset.revision 表示本地编辑；Batch.result_revision 表示成功发布一次结果；SyncRevision.revision 表示源数据同步修订。不能互相替代。
- 新增单行 `data_epoch`，任何可改变查询结果或字段口径的已提交写入推进 epoch；查询缓存键包含 epoch。同步修订和 epoch 通过 SQLite/PostgreSQL 触发器覆盖 ORM、COPY、bulk SQL，禁止仅由 UI 操作推进。SyncRevision 保持单调，不要求一批次恰好加一。
- 迁移与身份回填完成前不得开放新版快照、跨端删除和全筛选导出能力。

修订触发器的UPDATE必须检查业务字段实际改变：Batch中的batch_no/mapping_id/频段/deembedded/deembed_method/process_type/file_path/device_count/result_revision/deleted_at及源身份，Device的查询数据/源身份/曲线路径，Mapping/Process的业务字段才推进。Batch.task_id/active_run_id/synced_at/purge_after、run心跳/状态、draft/receipt/本地隐藏操作不推进源修订或query epoch。替换现有对batches所有UPDATE无差别推进的触发器，避免开始处理就使自己的base revision失效。source_inputs注册/回填在创建run捕获base之前完成；staging只写临时表。比较新旧业务值而非仅UPDATE OF，空更新不推进。

### 本次最终验收材料范围（2026-10-12用户确认）

原客户T8901P.01.zip已删除，B21原客户ZIP算法/SSE回归保留未验证并移出本次必需门禁。真实客户结果表仅作为已有结果快照，验证映射/查询/完整导出/离线报告，不补造原始曲线、算法条件或SourceRef证据。两类执行器真实处理流程与PG百万行发布门禁继续有效；详见B21步骤。此决定不改变产品数据契约或发布原子性，也不构成部署授权。

## C02 处理表与任务状态

所有 UUID 存 Text，时间为 UTC，JSON 使用 SQLAlchemy JSON，避免 PostgreSQL 专有类型。

| 表 | 必需字段与约束 |
| --- | --- |
| processing_runs | id(UUID PK)、task_id(UploadTask FK/unique)、batch_id、kind、attempt、retry_of_run_id nullable、base_result_revision、base_source_revision、config_json、mapping_digest、algorithm_digest、input_digest、status、quality_status、heartbeat_at、lease_token、stage_dir、started_at、finished_at |
| processing_items | id(UUID PK)、run_id FK、source_device_key nullable、source_member、port nullable、unit_kind(file/device/table_row)、status、error_code nullable、result_digest nullable、attempt；unique(run_id,unit_kind,source_member,port的规范非null编码) |
| quality_issues | id(UUID PK)、run_id、item_id nullable、severity(error/warning/info)、code、message、details_json、created_at；message 不含凭据或堆栈 |
| staged_device_rows | run_id、source_device_key 联合 PK、payload_json（Device 白名单字段）、payload_digest |
| source_inputs | id(UUID PK)、batch_id、member_key、port、input_digest、current_relpath、original_filename；unique(batch_id,member_key,port) |

Batch 新增 `active_run_id nullable`、`result_revision default 0`、`deleted_at nullable`、`purge_after nullable`。active_run_id 由服务验证，不建立与 processing_runs 的循环外键。ProcessingRun.status 与 UploadTask.status 使用同一转换函数，保持同一事务更新。

ProcessingRun另存dispatch_phase(queued/extract/extract_handoff/compute/publish/finished)、executor_ids JSON（各stage的Celery ID或本地进程标识）、summary_json、published_count。阶段交接使用dispatch_phase，不增加status；report分开显示计算成功数与本次发布数，全量重处理失败published_count=0，不能把staging成功数当入库数。

状态固定为 `pending → running → success|failed`，`pending|running → cancel_requested → cancelled`。用户确认的唯一例外：尚未领取、dispatch_phase=queued且确认未入队的投递失败可执行pending→failed；必须以当前租约与状态CAS在同事务结束task/run、记录DISPATCH_FAILED、释放active_run_id并保留输入。投递结果不确定不得按确认失败结束，保留pending和可定位的投递错误。success 可以有 `quality_status=partial`；不能把部分失败丢弃后显示“全部成功”。quality_status 为 unknown/complete/partial/invalid。stage 继续复用 extract/deembed/metrics/done/failed，取消终态用 failed stage 配合 cancelled status；避免再增加第二套阶段名。

- 每次处理创建新的 UploadTask 和 ProcessingRun，不重置原 task；Batch.task_id 指向最近一次已接受任务，active_run_id 限制同批次只能有一个写入者。
- 普通进度更新仅允许 running 且 lease_token 匹配，不能撤销 cancel_requested 或终态；任务事件包含 schema_version=1、task_id、run_id、event_seq、status、stage、progress、summary。
- ProcessingItem.status 为 pending/running/success/failed/skipped/cancelled。文件分类与器件处理分别计数；summary 中 file_counts 与 device_counts 分开，每组包含 total 和各状态数，不能把一个文件的两个端口算作两个文件。
- `WORKER_HEARTBEAT_SECONDS=5`、`WORKER_STALE_SECONDS=120`、`CANCEL_GRACE_SECONDS=10`、`STAGE_INSERT_CHUNK=1000`、`STAGING_RETENTION_HOURS=24` 进入 config；长计算采用独立心跳，不把进度变化当唯一活性依据。
- lease 每次领取/恢复改变 token；发布、阶段写入和终态转换必须重新读取/条件更新核验 token。过期 worker 仅可写自身 staging，不能写活动 Device。

## C03 一次上传、两阶段检查

新增 `upload_drafts`：id、group_id(UUID indexed)、request_key unique、status、storage_path、input_digest、original_name、upload_options JSON、report JSON、created_at、expires_at、consumed_task_id nullable。多文件组内每文件独立draft/request_key。GET /api/uploads/groups/{group_id}返回全部draft与已消费task摘要，刷新可恢复。status为preparing/ready/failed/consumed/expired，未消费草稿保留24h；已消费draft保留追溯摘要。

1. `POST /api/uploads/prepare`，沿用现有文件Form字段，另有request_key与group_id UUID。仅接收一次，保存文件并分发预检查，返回202 `{draft_id,status:'preparing',poll_url}`。
2. `GET /api/uploads/drafts/{draft_id}` 返回状态、准备进度、check_digest、检查报告、建议批次名、过期时间、consumed_task_id。前端每 1s 轮询，离开页面停止轮询。
3. `POST /api/uploads/drafts/{draft_id}/commit` JSON `{request_key,check_digest,acknowledged_warning_codes:string[]}`。服务端同事务检查状态、过期、报告摘要和警告确认，创建 batch/task/run 并将草稿标记 consumed，返回原 UploadAccepted 加 run_id；重复提交返回同一 task_id。
4. fatal 错误不允许 commit；输入/对照表/目标版本改变使检查失效，返回 409 PRECHECK_STALE，允许复用已上传文件重新检查，不要求重传。
5. 提供 `POST /api/uploads/drafts/{draft_id}/recheck` 仅更新可修正选项并产生新 check_digest；旧 commit 摘要失效。`DELETE /api/uploads/drafts/{draft_id}` 取消未消费草稿，运行中的预检查先请求停止、确认无写者后清理。

复用 extract 的安全解压流程到草稿独立目录；预检查可解压但不计算指标，commit 后转移该目录的管理归属，不再次解压。校准依赖检查复用 pipeline/calibration 的匹配函数，不另写文件名前缀猜测。检测文件/端口、mapping 命中、缺校准件、非法频率范围、重复成员、表格列契约和目标版本。频段合法性用实际文件头/频率扫描；mapping 未命中默认 error，首版不允许自行忽略。

错误级别固定：非法路径/重复成员/非法范围/无可处理输入/缺去嵌校准/必需表格列缺失/mapping无匹配/目标版本冲突为fatal，不消费draft。单个坏Touchstone或无关文件为可排除warning，确认后允许首批partial入库；坏输入登记failed或skipped并保留问题。旧批次重新提取若排除使预期器件不完整，仍按C04全替换门禁失败不发布。

兼容旧 POST /api/uploads：复用同一检查函数，但维持现有响应形状；warnings 按既有行为继续并进入质量记录，fatal 返回 422 或任务明确失败。目录监听、sync push、表格导入全部经过同一检查/结果记录。上传成功且算法错误的文件保留，可修正选项重检。

HTTP：400 格式错误；404 不存在；409 状态/版本冲突；410 草稿过期；413 容量超限；422 输入质量错误；503 队列不可用。标准 detail 为 `{code,message,retryable,context}`，已有 formatApiError 兼容字符串 detail。路径详情仅提供相对路径。

## C04 发布、保留与重试

- 计算写 `staged_device_rows`，文件写 `files/.runs/<batch_uuid>/<run_uuid>/`；不能 `rmtree` 当前活动目录。recompute 复用活动源文件；redeembed/reextract 生成独立目录。
- 提取开始不能更新 Batch.file_path、删除 Device 或删除 raw_zip；文件树也只在发布时更新。KEEP_RAW_ZIP 仅在成功发布后按保留策略生效，取消/失败始终保留输入。保留原始输入树供 redeembed，不以原 ZIP 已清理推断无原始数据。
- 发布函数 `publish_run(session,run_id,lease_token)` 使用专用 fresh Session、PG FOR UPDATE 或 SQLite BEGIN IMMEDIATE。检查 active_run_id、预期 result/source revision、未删除、未取消、校准/mapping 摘要仍一致，然后按 source_device_key upsert 现有 Device，保留其整数 id；创建新设备、删除不再存在的设备均处于同一事务。
- recompute 只更新明确指标字段；失败器件沿用旧指标并在报告标记。重新提取/去嵌/改 mapping 全量发布要求全部预期器件成功，否则保留旧活动结果且 run failed，报告列出失败项。首次上传可发布成功器件形成 partial，但零成功则 failed。该策略不得由 Luna自行更改。
- file tree 预先扫描成 manifest，事务内用无内部 commit 的方法批量更新；旧 reader 已取得的路径不立即删除。提交后才能更新统计缓存/物化视图、通知同步；它们失败不回滚已提交发布，但记录可恢复事件。
- 删除旧结果文件前需等待 `RESULT_FILE_RETENTION_HOURS=24`，并排除任何活动 run、正在读取的下载/导出租约；失败清理进入 CleanupJob，DB 先记录意图，再删文件，最后标完成。
- retry 端点 `POST /api/tasks/{task_id}/retry` JSON `{request_key,mode:'failed',item_ids?:string[],expected_source_revision:number}`。在 batch lock 内验证旧 run、输入摘要、当前版本；重复 request_key 返回同一新 task。
- 补算失败项以当前成功活动结果作为 base，合并未变的成功器件；如源/mapping/配置变化，返回 409 RETRY_INPUT_CHANGED，用户需选择整批新处理。原 run 与问题记录不修改。

## C05 查询与结果说明

```ts
type QuerySpec = { filters: Record<string, unknown>; fields: string[]; order_by: string | null };
type ResultManifest = {
  schema_version: 1;
  query_spec: QuerySpec;
  source_refs: SourceRef[]; // 批次级引用
  matched_total: number | null;
  total_relation: 'exact' | 'lower_bound' | 'unknown';
  returned: number;
  truncated: boolean;
  sampling: { method: 'none' | 'systematic'; rate: number | null };
  local_edits: number;
  excluded_rows: number;
  generated_at: string;
  consistency: 'verified' | 'unverified';
};
```

QuerySpec 保留现有 filters 字典或 AND/OR 树，两种格式标准化为树后计算摘要；不加入图表 limit/sampling。默认排序增加 Device.id 作为最终稳定次序。内部辅助来源字段不受用户 fields 选择影响，分开放到行 `_source_ref`，CSV 正文默认不加入内部字段。

QueryResponse 保留现有 total 等兼容字段，新增 manifest；skip_count 且截断时 manifest.matched_total=null、total_relation=unknown，旧 total 可保留 lower bound 但新 UI 只读 manifest。有精确 count 时 exact。source refs 与数据在同一读事务获得，来源变更后返回 409 QUERY_SOURCE_CHANGED，不显示旧数据的新版本号。

排除/本地编辑是项目操作，不修改服务器。每次首次修改保留原值以支持对账；excluded_rows 是被排除的唯一 source key 数，local_edits 是有修改的唯一行数。derived/empty 数据源允许 unverified，不能伪造来源。

质量规则 `QualityRuleV1={id,version,label,predicates:[{field,op:'gte'|'lte',value,unit}],combine:'and'}`，缺值记 unknown；pf 只是原测试标志，不能等同自定义合格规则。标准字段单位复用 query fields 元数据。

AnalysisSpec固定以下字段，后端schemas/analysis.py与前端types/platformContracts.ts保持一致：

```ts
type AnalysisSpec = {
  schema_version: 1;
  query_spec: QuerySpec;
  plot_limit: number;
  chart: {
    type: 'scatter'|'box'|'violin'|'swarm'|'line'|'wafer';
    x_fields: string[];
    y_fields: {name:string;aggregation:'all'|'mean'|'min'|'max'|'p50'|'p25'|'p75'}[];
    z_field: {name:string;aggregation:'all'|'mean'|'min'|'max'|'p50'|'p25'|'p75'}|null;
    z_mode: 'facet'|'color';
    wafer_z: {name:string;aggregation:'all'|'mean'|'min'|'max'|'p50'|'p25'|'p75'}|null;
    wafer_facet: string|null;
    show_data_points: boolean;
  };
  units: Record<string,string>;
  quality_rule: QualityRuleV1|null;
  comparison: {
    scope: 'source'|'workspace_snapshot';
    a: {source:SourceRef|null;dataset_id:string|null;query_spec:QuerySpec};
    b: {source:SourceRef|null;dataset_id:string|null;query_spec:QuerySpec};
    metrics: string[];
    group_fields: string[];
    common_frequency_ghz: [number,number]|null;
    condition_policy: 'require_equal'|'common_subset';
    wafer_metric: string|null;
    unified_color_scale: boolean;
  }|null;
};
```

聚合名从Explore现有值映射到上面枚举，未知值导入报错；应用模板先校验字段/单位。source比较a/b.source必填且为批次引用，dataset_id=null；快照比较反之。group_fields在源模式固定为eg/fl/ag/s_param_port，工作区可选已存在structureType+s_param_port；服务器Device没有structureType，不能发送虚拟结构字段作为SQL列。规则UI的min/max映射为两个predicates，不新增第二种协议。不同提取频段的标量无法通过裁剪查询变为相同频段，必须有同频段重算结果或确实同条件子集。

### C05a 计算前只读条件审查（2026-10-10用户确认）

- `POST /api/analysis/compare/conditions` 使用与compare相同的ComparisonRequest，但不计算统计、不写数据库。按两端SourceRef/QuerySpec读取同一个固定版本快照，复用B18已发布ProcessingRun及当前指标payload证据验证；完成后独立核对epoch/来源，变化409。
- 响应 `{schema_version:1, endpoints:{a,b}, units}`；每端 `{source,query_spec,conditions,structures,matched_total,issues}`。conditions为null或 `{frequency_ghz:[number,number],deembedded:boolean,deembed_method:string,process_type:string,config_digest:string,algorithm_digest:string,mapping_digest:string}`；structures是完整匹配结果中的去重 `[eg,fl,ag,s_param_port]` 元组（含真实null）；issues为 `{code,message}` 数组。未知证据与超出审查读取预算返回明确issue、conditions=null、structures=[]、matched_total=null，不伪造相同条件。来源不存在410、请求版本过期409、非法字段/单位422。
- 前端展示各具体条件值和共同结构/端口元组，单位/频段/处理摘要差异不可一键忽略。共同subset仅适用于真实结构/端口交集，必须人工选择并按完整元组过滤，不能独立集合拼接出不存在的组合。
- 静态版不调用HTTP。发布manifest可增量包含 `devices_sha256`（gzip字节）、`devices_arrow_sha256`（Arrow原始字节）、`comparison_units`及 `comparison_sources:[{source,batch_id,conditions,proven_metrics}]`。全部证据在export_all既有只读SQLite事务中核验当前Device与已发布StagedDeviceRow payload/digest后生成；只证实全部当前行匹配的指标。加载按实际收到字节核验对应摘要（HTTP可能已解压）；旧产物缺失时unknown并禁止源比较，工作区快照模式仍明确自己的范围。请求按同一固定发布数据中的QuerySpec重读完整count/rows，结构集合来自实际筛选行；前后核对manifest变化。不得只信任旧consistency标签。

## C06 项目契约

项目JSON备份envelope为 `{format:'aln-analysis-project',schema_version:1,exported_at,project:{head,version,datasets}}`。head为projectId/currentVersionId/generation/name/kind(draft|named|snapshot)/updatedAt/lastOpenedAt/schemaVersion；version含projectId/versionId/schemaVersion/baseGeneration/status(draft|complete)/createdAt/spec/registryMetadata/pointStyles/activeTabId/manifests/chunkRefs/provenance/edits/exclusions/notes。详细类型由前端任务书2.1补充，此处命名优先。template独立记录id/name/spec，不携带数据/局部编辑/固定源引用；snapshot实际数据不自动刷新。

IDB数据库名 `aln_analysis_projects`、版本1；对象仓库为heads(key projectId)、versions(key [projectId,versionId])、chunks(key chunkId)、templates(key templateId)。heads含currentVersionId/generation；version记录draft/complete和chunkRefs。每块最多2000行；变动块复制写入，未变块引用已完成块，GC前检查全部引用。单个readwrite事务验证预期generation后切换complete指针；没有await网络或React回调放进IDB事务。dataset未变复用块，单格编辑只重写所属块；删除/排序会移动位置时重写受影响块。备份导入重新分块。

自动保存 debounce=1000ms，失败不得标为已保存；关闭/重启恢复最后 complete，UI 显示未保存时间。beforeunload 不承诺完成异步写入。若 IndexedDB 不可用，进入明确会话模式并提供文件导出。

项目导入 `PROJECT_IMPORT_MAX_BYTES=100MiB`、`PROJECT_IMPORT_MAX_ROWS=500000`，校验schema、数值/列/来源和行ID唯一性；未知格式拒绝，未知字段保留在受控extras，禁止导入凭据、任意HTML或可执行表达式。导入生成新项目和version UUID，旧source refs保持。首版自包含PNG报告最多20图、累计PNG字节≤100MiB，超限明确失败。首版凭据仅会话内存，不增加Electron凭据IPC，capabilities.credential_storage=false。

## C07 全筛选导出

- `POST /api/export/jobs` JSON `{request_key,format:'csv'|'xlsx',query_spec,expected_sources:SourceRef[]}` → 202 `{id,status,poll_url}`。
- `GET /api/export/jobs/{id}` → id/status/progress/rows/manifest/error/artifacts/expires_at；`POST /api/export/jobs/{id}/cancel` → 状态；`GET /api/export/jobs/{id}/artifacts/{name}` 仅允许从记录的文件名白名单下载。
- 2026-10-10用户确认ExportJob启动恢复保持C02：仅在确认旧写者停止后接管新租约，pending→running→failed，running→failed，cancel_requested→cancelled；活跃或停止状态未知保留原状态及说明，不以API重启推断Celery已停止，不自动重跑，成功完整文件保留。
- ExportJob 独立表，状态协议同 C02；任务队列采用 `dispatch_export_job`，LocalExportTask 与 LocalTask 分开，以 executor handler map 分发，不给 UploadTask 增加假的 batch_no。
- CSV 产物为 data.csv + manifest.json + 下载 ZIP；XLSX 产物说明页 + 多个 data_N 工作表，每表最多 1,048,575 数据行。manifest 必须给出实际行数。
- PG 使用只读 REPEATABLE READ；SQLite 使用 WAL 下显式 BEGIN 的同一连接快照；开始获取匹配批次完整目录与 epoch，事务内 count/排序/分块迭代 2000 行。结束以独立连接比较 epoch/来源，任何变化首版保守失败 SOURCE_CHANGED，临时产物不公开。同一事务不能为心跳提交，心跳通过另一 Session。
- 原图表 limit 不参与构建 export SQL；`EXPORT_MAX_ROWS=5_000_000`、`EXPORT_MAX_BYTES=2GiB`、`EXPORT_TTL_HOURS=24` 为首版默认。达到容量明确失败，不能截断成功。下载先完整落盘，外部读取也检查 success 与未过期。
- 旧 /export/csv 和 /export/xlsx 保留原语义、标明 legacy，新的“全部筛选结果”按钮只使用 job API。
- 静态版从固定发布 snapshot 读取，不调用上述 HTTP；单独 Web Worker 使用 DuckDB 迭代且按预算输出。首版静态 CSV ≤200000行、≤100MiB，超限明确不可导出并提供服务器/桌面入口；静态 XLSX 全量能力=false。这些是公开能力限制，不是静默截断。

## C08 回收站与同步协议

2026-10-10用户确认：B12只过滤Batch关联数据；现有三类Process工艺表是独立数据域，不受批次软删影响，不按名称猜测关联。批次删除/恢复须验证独立工艺记录不变。

- 首版回收站 30 天；DELETE /api/batches/{name} 改为 soft delete，返回204维持兼容，但不立即清理文件。预览 GET /api/batches/{name}/deletion-impact 返回器件数/原始文件可用性/作用域/active task/预计清理时间。
- 新增 POST /api/batches/{name}/restore、GET /api/trash；运行任务未确认停止前删除返回409。已清理不可恢复返回410。
- 新增GET /api/sources/batches/{origin_id}/{source_batch_id}/resolve，返回本地batch id/name、状态及SourceRef，用UUID定位后进入既有详情路由，源不存在404。POST /api/replica-preferences接收 `{source_ref,hidden:boolean}`，仅操作本地副本偏好，不更改源batch删除状态。
- 原始批次跨端复制保留SourceRef及全部SourceInput UUID；已有sync/push-batch增加可选source_ref JSON和source_inputs JSON文件字段（同字段兼容小型文本，避免multipart文本1MiB限制）。成员/端口/实际内容SHA256完整对应预检才接受；canonical member_key与传输current_relpath分开，原始UUID不因打包前缀改变。无精确证明返回冲突，不能用同名/相同条数重建身份。上传确认须核验服务器SourceRef与器件UUID集合，原本地文件保留。
- 显式表格复核 POST /api/desktop-sync/table-targets/{batch_id}/reconfirm 接收{request_key,source_ref}；保存新提交的目标版本和当前表格摘要，保留原base记录。相同键绑定相同内容；后续数据变化需新键重新核对，不能自动重放。GET /api/replica-preferences列出本地偏好供恢复显示。
- 同步 v3 独立路由 `/api/sync/v3`。catalog 明确 `complete:true`、server_id、epoch、batches(含状态/版本)、tombstones(含删/恢复版本)。只接受协议与身份一致的完整 catalog；缺项不作删除依据。
- Tombstone 主键 origin_id/source_batch_id，软删或永久清理后都保留版本记录；恢复推进版本并下发 active。旧 v2 客户端将 tombstone 作为 `ready:false` 的目录项保留，读取批次返回410；清理后也保留占位，不让老客户端通过“缺席”推断物理删除。
- v3 明确与 v2 协议共存，新增功能均用能力门禁；已删除设备/文件 API 统一拒绝，不能只从列表隐藏。
- LocalReplicaPreference={origin_id,source_batch_id,hidden:boolean} 独立表；仅移除本地副本不调用远端删除，未来同步不重新物化该副本，用户主动取消隐藏才恢复。
- source_inputs、处理记录和 tombstone 在物理清理后保留必要追溯摘要；大 staging payload 和文件按租约清理，不能把所有记录随 Batch cascade 删除。
- 当前 synchronize 的 table-target 自动更新 revision 行为移除；409 保存原 expected revision，显式返回冲突。提供用户“重新核对并提交”的独立操作与新 request_key，不能自动重放覆盖。

## C09 能力与同步状态

`GET /api/capabilities` 返回 schema_version=1、mode:'server'|'desktop'|'static'、database:'sqlite'|'postgresql'|'duckdb'、protocols、features:{upload_precheck,processing_reports,projects,full_export_csv,full_export_xlsx,batch_compare,trash,replication_v3,remote_delete,credential_storage}、limits。后端仅返回server/desktop与sqlite/postgresql；static由本地能力适配器生成，不探测后端；项目存储另做运行时可用性检查。

```ts
type SyncStatus = {
  target: 'server' | 'web';
  state: 'local_only' | 'pending' | 'syncing' | 'confirmed' | 'conflict' | 'failed' | 'unknown';
  confirmed_revision: number | null;
  pending_revision: number | null;
  last_success_at: string | null;
  error_code: string | null;
  retryable: boolean;
};
```

新增 sync_operations 持久保存 source ref、target、request_key、状态、receipt、确认版本和时间；receipt 只能来自实际目标 readback。网页仅提交数据仓成功时为 pending，发布快照可读且版本/摘要一致才 confirmed。没有可核验 receipt 时 unknown，不以 synced_at 非空替代。

模型 metadata 返回 model_digest、训练范围、validation_metrics nullable、validation_dataset_digest nullable、真实/仿真标签来源；无已存在验证产物时 unknown，不为规划而重新训练。

### G02只读重试选项补充（2026-10-10用户确认）

GET /api/tasks/{id}/retry-options?offset=0&limit=100只读返回{task_id,run_id,expected_source_revision:number|null,retry_of_task_id:number|null,items,next_offset:number|null}。分页上限500、稳定item ID排序；items={id,unit_kind,source_member,port,status,error_code}。基准取原run的published_source_revision，否则base_source_revision，绝不替换为当前批次版本。复用B11失败/取消器件或表格行筛选，额外要求已有source_device_key或同批次member/port SourceInput证据；只对已结束run提供项。旧run/基准未知为空项，禁止安全重试。只返回相对成员路径；不读原文件、不创建任务、不改版本。POST retry继续独立校验输入、映射、配置和当前版本；409保留选项、不自动换基准。retry_of_task_id由原父run的task_id取得。

### C08远端影响读取与显式适配

G03复用现有v3 UUID来源操作，GET /api/sync/v3/sources/{origin_id}/{source_batch_id}/deletion-impact提供拥有端影响预览。桌面通过现有ServerClient/remote_access配置与server_id核验代理该读取及独立DELETE/restore；旧DELETE /batches/{name}对副本明确409，不能隐式远端删除。桌面普通批次impact仍为本地作用域；远端选项必须另读UUID影响并核对固定SourceRef版本，变化禁止删除，不自动重基。


### F16存储执行位置补充（用户确认，2026-10-10）

用户允许项目存储Worker：保持C06数据库格式、2000行块、generation CAS和complete指针同事务提交。主线程以分块编码与可转移缓冲区传输，避免整份项目或版本元数据通过postMessage结构复制。Worker复用既有IndexedDbProjectStore事务；错误代码、旧完整版本保护、块复用、多窗口冲突和跨端备份格式不变，必须重新实测P4双容量与真实IDB回归，不能因新增Worker直接宣称性能通过。
