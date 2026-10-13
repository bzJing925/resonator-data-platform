# Luna前端基线保护记录

本文件只记B00/F01开始前已存在的前端差异；SHA256完整清单在`platform-optimization-execution-log.md`指向的仓库外临时目录。

| 路径 | 开始前已存在的修改 |
| --- | --- |
| `frontend/electron/main.ts` | native bundle检测、Windows打包后端启动、避免结束非托管实例、更新行为区分 |
| `frontend/src/App.tsx` | 标题栏读取桌面版本、桌面/网页/静态版本文案 |
| `frontend/src/api/endpoints.ts` | 双向同步端点与结果类型扩充 |
| `frontend/src/components/ServerSyncPanel.tsx` | 端口提示、双向同步反馈、原始文件保留文案 |
| `frontend/src/components/ServerAutoSync.tsx`、对应测试 | 自动同步状态/执行行为调整 |
| `frontend/src/pages/Upload.tsx` | 入库/同步体验调整 |
| `frontend/src/pages/BatchDetail.tsx`、`Batches.tsx`、`Mappings.tsx` | 桌面服务器副本及批次/映射操作调整 |
| `frontend/package.json`、`package-lock.json` | 既有依赖变化 |

上述差异不得重置、整体重写或整文件格式化。若后续步骤需修改，先重读当时最新diff，以函数级增量完成；不得将用户在途逻辑算作本轮实现。

F01基线命令结果在执行后追加于下方。

## F01验证结果

- 保护清点：`git status --short`、`git diff --stat`及指定的四个前端差异文件已检查；HEAD与75个既有文件SHA256清单见执行日志，无既有差异被改写。前端既有源码差异为本文件表格所列路径。
- V：临时前端副本中`npm run typecheck`通过；`npm test -- --reporter=dot`为21个测试文件通过、1个跳过，130 passed、1 skipped。
- 桌面构建：`npm run build`通过；静态构建：`VITE_STATIC=1 npm run build`通过。两次构建的大包提示（Plotly chunk约4.9MB）和npm镜像配置弃用警告仅记录为既有告警。
- E：`npm run test:e2e -- plotly.spec.ts`为6 passed，包含scatter、box、violin、swarm、line渲染及React重渲染检查。
- 数据产物及构建输出位于`/var/folders/fr/k3vpq27d5sd5zsy0ll16r_ym0000gn/T/aln-platform-baseline-7dwr_sil/frontend`。单测首轮的WASM模块加载受临时副本对依赖软链接的访问限制；只在临时Vite配置增加依赖allowlist后重跑通过，仓库配置未改。
- 单测中出现JSDOM未实现Canvas `getContext()`提示；相关用例仍通过。桌面、静态构建告警不影响本步骤验收。
