# 平台优化 Git 复查与 B03 解决方案

日期：2026-10-09。范围：核查B00/F01/B01/B02实际修改及B03阻塞原因，提出修复方案。本轮没有实施业务修复、提交、push、部署或发布。

## Git检查结果

| 检查 | 实际结果 | 判断 |
| --- | --- | --- |
| HEAD与暂存区 | HEAD为基线 `ab9bb94a6cc086920e1828c32ce2c75f96060263`；暂存区为空 | 无新增commit或混合stage |
| 既有文件保护 | B00摘要75项，73项完全一致，2项变化，0项缺失 | 两项变化均为B02已记录的增量；其他源码、锁文件、前端、既有部署/打包资产未被本任务改写 |
| 两个混合脏文件 | `backend/app/desktop_setup.py` 增加v5共享升级入口，保留原有拒绝降级与复制初始化；`backend/app/models/__init__.py` 注册新模型，保留原有replica导入 | 需要保留增量hunk边界，不能整文件提交 |
| 本任务新增tracked差异 | 相对受保护清单新增 `backend/alembic/env.py`、`backend/app/models/{batch,device,task}.py` | 与B01/B02契约、迁移职责对应；env.py是临时PG验证暴露的ConfigParser百分号转义修复 |
| Alembic链 | `uv run alembic heads`只有 `4b38220ae4eb`，父为 `2026_09_15_120000` | 符合连续单链要求 |
| 格式/静态检查 | `git diff --check`、全后端Ruff通过 | 不能据此代替运行验收 |

审阅文档新增前，Git相对HEAD显示34个tracked文件、445 insertions/111 deletions；其中包含B00的30个既有脏文件。Git diff不包含untracked源码，审阅已另外读取新增模型、schema升级服务、状态服务及测试。既有 `dist-server-pack.zip`、`deploy/native-v2/`、`tmp/` 属于受保护资产，不能因本任务禁止生成数据产物而删除它们。

## 门禁复测与问题

复测命令从backend执行，DATA_ROOT、SQLite URL、pytest cache/basetemp在 `/tmp/aln-platform-git-audit-*`；PG使用 `127.0.0.1:55432/aln_test` 临时数据库，每次migration测试单独创建随机schema并finally清理：

```bash
uv run pytest tests/services/test_task_state.py tests/api/test_contract_examples.py \
  tests/models/test_platform_schema_upgrade.py tests/test_desktop_mode.py \
  tests/workers/test_copy_insert.py -q
```

实际结果：51 passed、1 failed、1 warning，无skip。告警为已有Alembic path_separator弃用提示。B01契约/状态、SQLite新库及v4升级/幂等/失败回滚/高版本拒绝、临时PG迁移、desktop专项检查均通过。

### 1. 新增COPY回归：必须先修复

`tests/workers/test_copy_insert.py::test_copy_columns_match_device_table` 在第75行失败：Device已新增 `source_device_key`，`backend/app/services/device_ingest.py::DEVICE_COPY_COLUMNS` 尚未包含该字段。

影响：提供UUID的行在小批量ORM路径可以携带该字段，COPY路径不会写入它；大批量入库会丢失源器件身份。B03虽规划补齐该列，当前工作树仍存在由B02新增模型字段造成的回归，不能将其归为旧失败或仅以B02专项测试通过判定全部门禁通过。执行日志已把B02重开为blocked。

修复动作：在COPY列尾部按Device列顺序追加 `source_device_key`，验证write_row确实传递UUID；补充真实临时PG COPY读取验证，并核对小批量ORM与触发COPY阈值的批量路径均保留输入UUID。仅缺来源身份的旧数据允许NULL，不能为了测试通过临时生成新UUID。B05的提交/savepoint重构仍按原顺序执行，不在此次字段修复中提前改事务架构。

### 2. B03文件树问题：建议统一为实际路径语义

现有模型明确文件树为虚拟组织，移动只改parent_id，重命名只改name；FileNode.relpath与Device.s_param_path指向实际文件。C01/B03的“文件树移动/rename同步current_relpath”没有区分虚拟组织与实际路径变化，存在措辞与现有行为不一致。

建议采用以下规范文本，先同步修订C01与B03：

> SourceInput.current_relpath记录实际解析产物的POSIX相对路径。虚拟文件树的移动、显示名称修改、排序只改变展示结构，不修改current_relpath、Device.s_param_path、member_key或源器件UUID。真实解析产物路径变化时，更新current_relpath，并保持member_key、input_digest对应的原始输入及源器件UUID；活动Device.s_param_path和文件树manifest与结果在同一发布事务中切换。失败或取消保留旧活动路径。新run的staging路径不得提前覆盖活动current_relpath。

这延续既有文件树职责。B05已有的UUID管理目录继续保持稳定，批次重命名只修改显示名；旧目录兼容路径依照B05处理，批次基目录变化不等同于批次内相对路径变化。

B03新增测试应区分两类行为：虚拟移动/重命名后仍能从原真实路径读取，UUID与member_key/current_relpath不变；真实解析产物路径映射变化后只更新current_relpath并复用UUID。B09发布测试进一步验证该切换与Device/file tree结果同事务，失败/取消保留旧路径。

### 3. 执行记录需要准确归属

原B02日志将 `sync_identity`、`sync_revisions`、`replica_batches`、`table_receipts` 写成新增交付。它们实际是B00既有受保护在途模型；SQLite create_all注册它们，而新PG Alembic没有创建这四表。PG元数据与触发器初始化扩展属于B03，不能在B02宣称完成。日志已更正，且修复了错误的前端基线文件链接和受保护文件数量。

## 建议恢复顺序

1. 先按上述实际路径语义同步修订C01、B03及相关测试验收文字；不修改现有虚拟树路由的物理文件行为。
2. 修复COPY字段回归，运行既有COPY测试、真实临时PG COPY身份验证、B01/B02专项检查与Ruff；全部通过后恢复B02 passed。
3. 按原B03顺序实现持久origin身份、源输入登记和分批回填、旧副本未知来源说明、快照UUID/修订透传、双数据库修订与epoch触发器、缓存失效。操作字段与staging不推进revision/epoch的契约保持不变。
4. 运行B03指定检查及真实PG integration；通过后写日志，继续B04。发生新失败立即记录并停止对应步骤。

本次未改前端业务代码，因此未重复其既有检查；三端前端F16与两数据库执行器B21尚未到达，整体任务仍未完成。临时PG schema迁移通过不能代替PG+Celery/Redis流程验收。
