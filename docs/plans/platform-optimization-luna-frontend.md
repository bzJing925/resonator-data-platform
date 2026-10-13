# Luna 前端任务书：T5 / T7 / T8 / T9

日期：2026-10-09。状态：实施前任务书。本轮只写文档，未修改业务代码、运行业务测试、提交、部署或发布。组合执行顺序以入口任务书为准；依赖未满足时停止对应步骤，禁止用 mock 对外宣称能力完成。

## 1. 核实的实现基线与保护范围

以下是当前工作区读取结果，不代表已发布：

| 已有文件/符号 | 当前行为 | 约束 |
| --- | --- | --- |
| `frontend/src/features/analysis/workspace.tsx` / `AnalysisWorkspaceProvider`, `workspaceReducer`, `loadQueryRows`, `nextDatasetIdentity` | 内存 reducer；`analysis-N` 进程序号；history 在状态内 | 无项目持久化；恢复后必须避免 ID 撞号 |
| `model/dataset.ts` / `createAnalysisDataset`, `duplicateAnalysisDataset`, `stableRowId` | rows/revision/fieldMap；rowId 可来自本地数字 id；复制重新分配 rowId | rowId 是工作表身份，不是跨端源器件身份 |
| `model/commands.ts` / `executeAnalysisCommand`, `executeDeleteRows`, `executeUpdateRows` | 更新/删除已有命令 | 必须复用，不建立第二套选中或撤销状态 |
| `frontend/src/pages/Explore.tsx` / `usePageState('explore', EXPLORE_INITIAL_STATE)`, 查询分支、`onExportCsv` | 配置 localStorage；原始查询 skip_count=true；CSV 是当前工作表 | CSV 包含局部修改；不能标全量源数据 |
| `frontend/src/contexts/PageStateContext.jsx` / `serializeStateMap`, `enforceDataLimit` | 500ms 保存，默认数据上限 1MiB，Set/Map 序列化 | 不是完整项目库；新项目不得重复写这里 |
| `frontend/src/components/Charts.tsx` / `UnifiedChartGrid`, `WaferMap`, `buildUnifiedSpec`；`chart/chartScene.ts` / `buildJmpChartScene` | 共用 Plotly 场景、联动、图表 | 报告复用 scene，不重新推算图形统计 |
| `frontend/src/api/endpoints.ts` / `queryDevices`, `queryAggregate` | IS_STATIC 分流；同步 API 直接 HTTP | 新增 API 同样必须分流或能力禁止 |
| `frontend/src/api/static/index.ts` / `queryDevices`, `queryAggregate`, `exportCsv` | DuckDB；查询/CSV 200000 上限；总数可 null | 到上限时 truncated 只是保守判断，不证明完整 |
| `ServerSyncPanel.tsx` / `refresh`, `run`；`ServerAutoSync.tsx` | 桌面状态及定时双向同步；问题用消息显示 | 新设置复用调用，避免两个轮询器 |
| `frontend/src/types/index.ts` / `ServerSyncStatus`, `ElectronAPI`, `SurrogateStatus` | 状态不含逐资源 receipt；无凭据 IPC；模型说明不完整 | 不从 reachable/commit_url 猜同步确认或发布生效 |
| `frontend/electron/preload.ts` | backend/分析命令/剪贴板 IPC | 当前没有 secure credential 保存能力 |
| `frontend/src/App.tsx`, `Sidebar.tsx`, `Dashboard.tsx` | 已有路由、静态路由禁止上传等；首页现有统计 | 增量加入口，不能破坏启动 BootGate |

在途已修改文件包括 `frontend/electron/main.ts`, `frontend/package{,-lock}.json`, `frontend/src/App.tsx`, `api/endpoints.ts`, `components/ServerAutoSync{,.test}.tsx`, `ServerSyncPanel.tsx`, `pages/BatchDetail.tsx`, `Batches.tsx`, `Mappings.tsx`, `Upload.tsx`。backend replication v2 也在途。禁止 reset/checkout 覆盖、整体重写、格式化这些文件；需要触及时先比较 F01 基线并仅作小范围增量。依赖后端契约按主任务书交接，不在本文件安排后端实现。

## 2. 固定契约与设计决定

共用字段唯一来源为 [共用契约任务书](platform-optimization-luna-contracts.md)。前端落点拟新增 `frontend/src/types/platformContracts.ts`，由 T1 负责；本文件只消费，不能重复定义不同契约。

- SourceRef 固定 `{origin_id:string,source_batch_id:string,source_device_key:string|null,source_revision:number|null}`；批次/器件 ID 是持久 UUID。natural key 仅用于对账；文件路径/批次显示名变化不改变身份。来源缺 UUID 的旧数据标 legacy/unverified，不在浏览器猜生成跨端源身份。
- QuerySpec 固定 `{filters:现有AND/OR树,fields:string[],order_by:string|null}`；图表 limit 不属于它。
- ResultManifest 固定 `schema_version:1,query_spec,source_refs,matched_total,total_relation,returned,truncated,sampling,local_edits,excluded_rows,generated_at,consistency`；total_relation 为 exact/lower_bound/unknown；sampling.method 为 none/systematic，rate 可 null；consistency 为 verified/unverified。未知不变 0；source_revision 不等于 dataset.revision 或内部 result_revision。
- AnalysisSpec.schema_version 固定 1；具体字段按共用契约。分析配置、筛选、图表、单位与规则统一归项目；PageState 仅保留其他页面和轻量 UI 偏好。
- SyncStatus 按共用契约消费；处理完成、服务器确认和网页发布是不同目标。旧端缺能力显示未知或不可用。

### 2.1 客户端项目 schema（拟新增）

`projectTypes.ts` 定义以下本地结构；其内部 revision 不替代源 revision：

```ts
type ProjectKind = 'draft' | 'named' | 'snapshot';
type JsonValue = null | boolean | number | string | JsonValue[] | {[key:string]:JsonValue};
type RegistryMetadata = Omit<DatasetRegistry,'byId'> & {
  byId:Record<string,Omit<AnalysisDataset,'rows'>> };
// DatasetRegistry、AnalysisDataset、PointStyleState 从现有 model 模块导入。
interface ProjectHead { projectId:string; currentVersionId:string; generation:number;
  name:string; kind:ProjectKind; updatedAt:string; lastOpenedAt:string; schemaVersion:1 }
interface ProjectVersion { projectId:string; versionId:string; schemaVersion:1;
  baseGeneration:number; status:'draft'|'complete'; createdAt:string;
  spec:AnalysisSpec; registryMetadata:RegistryMetadata; pointStyles:PointStyleState;
  activeTabId:string; manifests:Record<string,ResultManifest>;
  chunkRefs:Record<string,string[]>; provenance:Record<string,SourceRef|null>;
  edits:LocalEdit[]; exclusions:ExcludedRow[]; notes:string }
interface LocalEdit { datasetId:string; rowId:string; column:string;
  before:JsonValue; after:JsonValue; source:SourceRef|null }
interface ExcludedRow { datasetId:string; rowId:string; source:SourceRef|null;
  reason:string; originalRow:Record<string,JsonValue> }
```

所有导入数据从 unknown 经运行时校验收窄到以上类型；DatasetRegistry 去 rows 元数据与 PointStyleState 复用已有类型，不允许类型断言绕过导入验证。Set 转数组；非有限数转 null 并记录校验 warning；日期 ISO UTC；只支持 JSON-safe cells。selection/hover/cache/graphDiv/history 不保存，恢复 selection 清空，history=createHistoryState()。删除需保存原始行与 reason，不能只剩一个计数。

IndexedDB 名 `aln_analysis_projects`、DB version=1，stores：`heads` key projectId、`versions` key [projectId,versionId]、`chunks` key chunkId、`templates` key templateId。rows 按 2000 行分块；同 dataset.revision 未变时复用 chunkRefs，单格编辑记录dirty rowId只重写所属块；删除/排序重写受影响块，禁止整表重新序列化。版本 immutable；snapshot 禁止原地编辑，编辑先另存为 named。默认草稿名“未命名项目”。dataset 新 ID 用 crypto.randomUUID，不改变已有 rowId。

`ProjectStore`：`list():Promise<ProjectHead[]>`、`load(projectId):Promise<ProjectVersionWithRows>`、`save(input,expectedGeneration:number|null):Promise<ProjectHead>`、`remove(projectId,expectedGeneration):Promise<void>`、`saveTemplate(spec,name)`、`listTemplates()`。save 返回 complete head 才允许显示已保存；错误码 `UNAVAILABLE/QUOTA/CONFLICT/CORRUPT/UNSUPPORTED_VERSION`。

保存流程：先写 draft 与 chunks；最后同一 readwrite transaction 比较 heads.generation，标 complete 并切 head。expected=null 只允许新 ID，不允许覆盖；冲突 abort，保留旧 head。DB transaction complete 才 resolve。启动只读取 complete；清理无引用 draft/chunk 在单独维护事务中执行，不删除 complete 的引用。保留当前及上一 complete，所有其他未引用 complete 可清理；显式 snapshot 各有独立 head。

JSON 备份 envelope `{format:'aln-analysis-project',schema_version:1,exported_at,project:{head,version,datasets}}`；导入 always new projectId/versionId，保持 dataset/rowId/source identity，先完整校验再一次提交。上限 100MiB 文件/500000 行，不上传；超过明确拒绝。只打包表格快照、配置、备注和来源，不打包曲线/凭据/history。模板只有 AnalysisSpec 与名称，无 rows/source_refs/local edits/排除器件 ID。

### 2.2 对比口径

固定两个来源 cohort A/B，按 SourceRef 批次选取；允许相同来源自比较。比较完整 QuerySpec 结果或明确标注的已保存工作区快照，首版不允许“截断的源查询结果”冒充全批次。来源锁定/T6 完整读取缺失则源对比按钮不可用，仍可对比标明范围的工作区快照。

固定指标 fs_ghz/fp_ghz/qs/qp/k2eff_pct/zs_ohm/zp_ohm，由 FieldMeta 校验存在和单位；每指标剔除 null/非有限值且分别计 n。分布显示 n/min/max/mean/p25/p50/p75，分位为线性插值 `(n-1)p`；无有效值统计 null。差值为各同构分组 B.mean-A.mean，不逐器件伪配对；源模式按eg/fl/ag/端口分组，工作区可按已映射structureType/端口分组；不能把虚拟结构字段直接送SQL，其他指定差异过滤选择纳入 spec。

兼容审查要求端口、单位、结构、频段、处理配置摘要；未知不能视为相同。单位不同阻止该指标；不同已提取频段不能通过裁剪标量查询变成相同频段；需先有共同频段重算结果或确实同条件子集；找不到共同子集则停止。原始 pf 与自定义规则分栏，规则UI min/max转换为C05 QualityRuleV1.predicates，缺失结果 unknown 不算 fail，合格率分母仅规则可评估数。wafer map A/B 固定同字段同单位统一 finite min/max；不同wafer先分面，不跨wafer混合坐标；同wafer坐标重复按mean聚合，附n，不生成逐点差值地图。

### 2.3 报告与能力

自包含 HTML 采用 Plotly.toImage 输出 PNG data URL，保留离线静态图和全部统计说明，不嵌 CDN 或 Plotly runtime。报告锁定一个 complete 项目版本，截图期间 workspace 变化不混入；缺图导出整体失败，用户可明确取消图后重新生成，禁止 silently drop。HTML 转义所有备注/标题；无原始 HTML；CSP 禁 script/network，打印 CSS @page A4 landscape，避免图跨页。PDF 由用户浏览器打印，不显示服务端生成按钮。

T8新GET `/api/capabilities`来自B19；静态适配器按C09同形状features字段并mode=static、database=duckdb，IndexedDB探测失败后features.projects=false。第一版凭据只保留当前组件/会话内存，退出设置清除；不新增 secure-store IPC、不保存 token。服务器/桌面发布入口仅 capabilities 允许且人工提交时可调用已有 pushBatchToWeb；commit_url 仅代表提交回执，发布确认必须有 T8 后端版本状态，否则“等待确认/未知”。静态绝不请求 desktop-sync/web-sync。

## 3. 固定夹具与执行规则

拟新增 `frontend/src/features/analysis/__fixtures__/platformFixtures.ts`：

- P1：origin O1/O2，同显示批次名但两个 source_batch_id UUID；器件 UUID 独立，改文件路径后 SourceRef 相同。
- P2：A 的 qs=[10,20,null,40]，B=[20,30,40,null]；n 各3，均值 70/3 与30，差20/3；自比较差0；A p25=15,p50=20,p75=30。端口均 S11，频段1–3GHz，单位无量纲，结构 X。
- P3：增加 S22、单位 MHz、频段2–4、未知处理摘要各一行，检查阻止/共同子集说明；pf 与自定义规则相反；规则 qs>=20 则 A 有效规则通过2/3。
- P4：300001行确定性生成器，超上限旧路径应显示截断；T6 完成后完整路径返回300001，无重复。项目2001行跨两块，编辑第二块、删除一行、恢复与统计对账。
- P5：版本1→2，窗口A/B同 generation=1，B保存成功后A冲突；注入 QUOTA/transaction abort/draft残留/损坏块/未来schema=99。
- P6：同步 offline/409/处理中/服务器receipt确认/网页commit后未确认各状态；模型版本/训练范围/验证指标全缺；备注 `<script>alert(1)</script>`。

步骤验证命令统一从 `frontend/` 执行。新增测试文件须先实现才运行。V表示依次执行 `npm run typecheck`、`npm test`、`npm run build`；S=`VITE_STATIC=1 npm run build`；E=`npm run test:e2e -- <spec文件名>`。不运行 dist/electron:pack、bootstrap、发布脚本。每步失败先记录旧基线与新增失败，不能跳过失败继续联动下一步。当前文档编制未执行这些命令。

## 4. 顺序执行步骤

### F01：建立非破坏性基线（T0）

前置：读取仓库 AGENTS.md、主计划和共用契约。范围：仅新增 `docs/plans/platform-optimization-frontend-baseline.md`。执行 `git status --short`、`git diff --stat`、`git diff -- frontend/src/App.tsx frontend/src/api/endpoints.ts frontend/src/components/ServerSyncPanel.tsx frontend/electron/main.ts`；记录脏文件及 hash，不提交。实施阶段再运行 V/S 与现有 E=plotly.spec.ts，记录既有失败和环境缺项。验收：可区分当前改动、新改动及既有失败。失败：基线文件已存在不得覆盖，追加有日期记录；在途契约未协调停止修改对应文件。

### F02：消费 T1 共用契约、来源扩展

前置：T1 platformContracts 类型及样例已定。范围：增量 `model/dataset.ts`, `workspace.tsx`，拟新增 `model/provenance.ts`, `provenance.test.ts`, platformFixtures。新增 `attachSourceRefs(rows,manifest)`、`validateSourceRefs()`；AnalysisDataset 增 manifest/provenance/edit/exclusion 元数据；`loadQueryRows(rows,stats,manifest?)` 第三参数保持兼容旧调用。源键 tuple 序列化不以字符串冒号拼接引入歧义；缺失来源保持 null、consistency=unverified，不伪造 revision。复制数据保留 SourceRef，分配独立 workspace rowId，并拷贝相关局部变更归属。验收 P1 及旧 dataset tests；命令 `npm test -- provenance.test.ts dataset.test.ts workspace.test.ts` + typecheck。失败：API缺身份保留工作区功能但禁止绑定来源更新或完整源比较。

### F03：定义项目序列化和迁移

前置 F02。新增 `project/projectTypes.ts`, `project/projectCodec.ts`, `project/projectCodec.test.ts`。符号 `serializeWorkspace`, `restoreWorkspace`, `validateProjectBackup`, `migrateProjectBackup`；输出按2.1，恢复 history 与 selection 空。schema=1仅接受验证通过结构；未来schema明确 UNSUPPORTED_VERSION；旧 PageState 仅提取已知 Explore 配置映射为 AnalysisSpec，不声称恢复丢失 rows。校验引用、重复rowId、chunk顺序、registry order、字段合法性与JSON预算。验收 P4/P5，损坏 import 零写入；`npm test -- projectCodec.test.ts` + typecheck。

### F04：实现 IndexedDB ProjectStore

前置 F03。新增 `project/ProjectStore.ts`, `project/IndexedDbProjectStore.ts`, `project/IndexedDbProjectStore.test.ts`。符号 `openProjectDb`, `save`, `load`, `collectOrphans` 按2.1事务；不新增全局状态库。DB blocked/versionchange 提示关闭其他窗口，连接关闭后需重试；quota 失败保持 head及上一版。测试为真实浏览器 IndexedDB 放在新增 `e2e/analysis/projects.spec.ts`，unit只校验事务逻辑，不能用简陋 Map mock证明原子性。验收 P4两块/P5冲突及abort；命令 typecheck、unit、E=projects.spec.ts；恢复不能返回 draft。

### F05：接入 reducer 与自动保存

前置 F04。增量 workspace.tsx/model/commands.ts，新增 `project/useProjectAutosave.ts`, `project/ProjectSaveBadge.tsx`。增加明确 action `restoreProject`（恢复完整 present、空history）、AnalysisSpec所有权与配置更新 action；更换 nextDatasetIdentity 为UUID。Explore 内原 AnalysisSpec配置统一读workspace，不在PageState重复写；一次读取旧explore配置成功写 complete 后记录轻量迁移完成标记。autosave 状态 idle/dirty/saving/saved/error/conflict；最后编辑1000ms调度，单写队列、保存中的新变更继续dirty，返回旧结果不能标当前已保存。选择/hover不触发写入。页面卸载未保存给提示，不承诺 unload 异步写必成功。验收编辑/排除/撤销刷新后恢复最新complete，重启撤销不可用并明示；QUOTA红色失败；运行 workspace/codec tests + E projects + V。冲突 UI 只有“重载远端客户端版本”“另存为新项目”，禁止自动覆写。

### F06：项目/模板/固定快照与 JSON备份

前置 F05。新增 `project/ProjectMenu.tsx`, `project/projectBackup.ts`, `project/projectBackup.test.ts`，Explore工具栏接入口。固定按钮新建/打开/重命名/另存为/保存模板/应用模板/固定快照/导入JSON/导出JSON；打开 dirty 项目提供保存/放弃/取消；新建不删除现有head。模板应用到新草稿，不能覆盖当前编辑；snapshot读取不可变、编辑另存。导出 complete 已提交版本，用户需先保存dirty；项目不含凭据或原始曲线且 UI 明示。导入新ID、完整校验、单次提交，无 overwrite选项。验收 P5未来版本拒绝、跨三端JSON互导rows/配置/来源一致，token扫描无结果；unit+E projects+V/S。

### F07：查询范围与异步结果防混（消费 T6）

前置 T6 ResultManifest/固定源版本读取已实现；否则本步骤门禁停止，只可显示旧范围未验证。新增 `result/resultManifest.ts`, `result/DataScopeBanner.tsx`；增量 Explore查询分支、api/endpoints/static适配。QuerySpec 与 plot limit分开；`validateQueryResult` 验 returned=rows.length、计数关系，`requestSequence` 丢弃迟到结果。unknown总数显示“返回N，总数未知”；truncated显示范围受限，consistency=unverified显示未验证。工作表编辑/排除由 ledger更新manifest计数，不能改source_revision。新导出jobs用主契约 POST `/api/export/jobs`、GET `/api/export/jobs/{id}`，静态不能调用它们。验收 P4截断/全量及A慢B快只装载B；结果版本变化明确错误；unit+V/S。只接 T6确定契约，不新建第二套完整导出实现。

### F08：比较规格与兼容检查

前置 F06/F07，共用AnalysisSpec字段已可表达对比。2026-10-10用户确认C05a：源模式先调用只读compare/conditions，消费具体条件与issues；禁止为获取元数据而调用计算compare。静态仅消费同形绑定发布版本的条件证据，缺失unknown。新增 `comparison/comparisonSpec.ts`, `comparison/compatibility.ts`, `comparison/compatibility.test.ts`；输入两来源QuerySpec/manifest/批次处理元数据，输出 issues及共同subset，不改源数据。按2.2固定决策；源选择 SourceRef tuple不是batch_no；来源版本未知时源对比disabled。新增 `ComparisonSetup.tsx`，每差异显示具体值并要求选择共同子集，不能一键忽略单位。验收P1/P3，错误保留输入不发计算；`npm test -- compatibility.test.ts` + typecheck。

### F09：比较统计纯函数与完整读取

前置 F08，B18源比较服务和固定版本完整读取可用。新增 `comparison/comparisonStatistics.ts`, `comparison/comparisonStatistics.test.ts`, `comparison/comparisonData.ts`；函数 `computeMetricSummary`, `computeMeanDifferences`, `evaluateQualityRule`, `loadComparisonCohorts`。JSON数值null非零；按2.2分位；各n、原始pf与自定义分开。源统计调用B18 POST /api/analysis/compare，body={comparison:spec.comparison,quality_rule:spec.quality_rule}，响应cohorts与统计在同一快照；静态端用同QuerySpec在DuckDB读count并按C07预算完整读取，超限明确拒绝，不重复使用queryDevices(limit=200000)。快照统计由工作区rows，ResultManifest明示本地编辑/排除。读取期间源版本变化整体失败保留上一比较结果、不交付混合cohort。验收P2/P3精确期望、容差绝对1e-9或相对1e-8；服务端及DuckDB与同夹具对账需T6提供，缺失保持门禁；unit+V/S。

### F10：对比分布/差值/wafer与选中联动

前置 F09。新增 `comparison/ComparisonView.tsx`, `comparison/comparisonScene.ts`, `comparison/comparisonScene.test.ts`，复用 buildJmpChartScene/UnifiedChartGrid/WaferMap；Explore挂载对比模板。两cohort作为registry datasets，图表customdata携 datasetId,rowId，用现有selectRows；不建比较selection。wafer统一色标、相同值色域加±max(abs(value)*1e-6,1e-6)，全部空无色域。点击分组差值选相应两组有效行，并显示选择范围。验收自比较0、缺失n、点击图表选表格/删除撤销图表同步；新增 E=comparison.spec.ts必须真实Plotly交互，scene unit不能代替；unit+E+V/S。

### F11：HTML报告数据冻结与图片导出

前置F10，T6 manifest可信；允许有截断的工作区报告但醒目标“当前工作区部分数据”。新增 `report/reportModel.ts`, `report/plotImages.ts`, `report/reportModel.test.ts`；输入complete版本+备注，`freezeReportInput`, `renderReportImages`；图像固定1600×1000 scale=1，逐图导出，生成失败无download。采用隐藏离屏Plotly.newPlot(scene)后toImage/png并purge finally，不能截图当前页面混入下一版本；必须控制同一图表库导入不再新增第二Plotly包。报告生成过程中停止修改报告冻结对象；manifest与统计均来自已存快照。验收变更workspace不影响冻结报告、graph错误全部失败并清理DOM；unit+typecheck。

### F12：安全自包含HTML、打印与模型说明

前置F11。新增 `report/reportHtml.ts`, `report/ReportDialog.tsx`, `report/reportHtml.test.ts`，增量 Surrogate.tsx/ml/surrogateOnnx.ts仅消费T9模型metadata；未知模型版本/训练范围/验证指标显示“未知/未提供”，无虚假置信区间。报告含标题/备注/生成时间/SourceRef与版本/QuerySpec/单位/规则/各n/完整manifest/图片/模型说明/原始曲线未包含/会话撤销不包含。escapeHtml全部用户内容，data URL只接受工具生成PNG前缀，不渲染外来URL；HTML CSP禁脚本及外部连接。验收P6恶意备注当纯文字；离线浏览器打开零网络图完整，打印A4 landscape无溢出；新增 E=report.spec.ts+unit+V/S。图导出超内存报明确错误，不缩掉图而仍称完整。

### F13：能力适配与统一同步状态

前置B19 capabilities与SyncStatus；F06。新增 `api/capabilities.ts`, `sync/syncStatusAdapter.ts`, `sync/SyncStatusBadge.tsx`, `sync/syncStatusAdapter.test.ts`；固定IS_STATIC优先；其他模式读取后端mode与database，electronAPI仅表示桌面壳不能推断后端数据库或server角色。GET `/api/capabilities`失败显示“能力未知，重试”，禁写操作，不因健康接口成功猜能力；静态本地capabilities不发HTTP。旧 ServerSyncStatus仅映连接/待回传，不伪造confirmed版本。按资源+target持久失败状态来源必须是后端，不把toast当状态库。每target分别显示pending/confirmed/error/conflict/unknown，共用SyncStatus精确字段按契约。验收P6及静态无写调用；unit+V/S。

### F14：设置路由与现有同步面板归并

前置 F13及T4删除语义后端完成。新增 `pages/Settings.tsx`, `sync/useSyncCoordinator.ts`，增量 App/Sidebar/ServerSyncPanel/ServerAutoSync/Batches，仅小范围修改在途文件。设置分连接配置/服务器同步/网页发布/本地项目存储/偏好；ServerSyncPanel改为共享组件，Batches仅链接设置，不再第二实例触发刷新。coordinator唯一轮询60s、inflight互斥、卸载取消后续写状态；partial errors显示失败资源，409 conflict禁自动重拉改revision重试；人工刷新可读取最新状态但不能触发覆盖。token只会话内存，静态无发布表单，不修改Electron凭据IPC。`pushBatchToWeb`按钮只在能力允许人工明确发布时调用，返回commit只显示已提交待确认。验收离线/409/partial errors/重复点击只请求一次、重开token空；ServerAutoSync旧tests更新保留原行为保障；unit+V/S，新增 E=settings.spec.ts。实现测试只用本地fixture端，不真实发布。

### F15：工作台真实汇总入口

前置 F14，T2质量汇总API/T4生命周期API契约就绪；缺API区块显示未提供而不写假列表。增量 Dashboard.tsx，新 `project/useRecentProjects.ts`, `sync/AttentionList.tsx`。最近项目按lastOpenedAt降序取5，点击恢复complete；任务失败来源listTasks，质量问题来源T2结构化summary，sync问题来源F13；每项带稳定资源ID+错误码+明确路由，任务 `/tasks/:taskId`，项目 `/explore?projectId=...`，sync `/settings?target=...`。批次同名跨origin不得只以batch_no定位；新先调用C08的GET /api/sources/batches/{origin_id}/{source_batch_id}/resolve，再进入既有批次详情；404显示源已不存在，保留项目快照不删除。静态显示最近项目、发布快照版本、只读模型信息；不列上传、重试、回收、双向同步。验收partial load单区失败、空列表不假0、刷新问题可定位；Dashboard unit+E settings/projects+V/S。

### F16：前端性能与三端验收

前置F15与G01–G04，主计划T0预算阈值已确定。新增 e2e上述spec并复用当前 analysis/benchmark.ts、performance.spec.ts，不自行宣称性能预算通过。固定P4，记录同设备首次保存/单格保存/恢复/报告生成耗时和峰值内存；验证保存未变dataset不重写、hover不写、无history持久化。静态：快照查询→编辑→项目保存刷新→比较→全发布快照导出(T6允许)→离线HTML；服务器：同流程+真实状态读取；Electron：重启同一持久profile恢复+实际后端能力显示。服务端完整源对比验收必须服务联调，不以mock代替。命令V/S、`npm run test:e2e`、必要时 `npm run test:e2e:perf`；桌面启动用 `npm run electron:dev`，不pack。失败按功能停止交付，并记录三端分别pass/blocked，不用单一“全通过”。

### F17：交接和可审阅结果

前置F16/B21及目标步骤通过，受阻步骤有明确原因。新增 `docs/plans/platform-optimization-frontend-acceptance.md`，记录步骤编号/实际文件/命令/结果/截图路径/三端能力/已知限制/数据清除风险/备份恢复路径。`git diff --check`及与F01基线对照，只描述本任务增量；命名项目IndexedDB也受站点数据清除影响，需JSON备份；旧库不把PageState当完整恢复来源。仅实施任务自身增量；提交须按入口任务书规则，不部署、不发布、不改生产数据；不得自动清理脏工作树或生成release artifacts。交接时必须清楚列出T1/T2/T4/T6/T8/T9尚未就绪依赖，不能用disabled UI宣称后端功能完成。


### F16存储执行位置补充（用户确认，2026-10-10）

用户允许项目存储Worker：保持C06数据库格式、2000行块、generation CAS和complete指针同事务提交。主线程以分块编码与可转移缓冲区传输，避免整份项目或版本元数据通过postMessage结构复制。Worker复用既有IndexedDbProjectStore事务；错误代码、旧完整版本保护、块复用、多窗口冲突和跨端备份格式不变，必须重新实测P4双容量与真实IDB回归，不能因新增Worker直接宣称性能通过。
