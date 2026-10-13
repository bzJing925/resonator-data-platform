# 绘图范围、性能与取消选择修复执行记录

日期：2026-10-13。源码版本保持 0.3.5；本记录对应源码修复和本机验收。

## 工作区保护

开始时工作区已有 316 项修改或未跟踪路径。临时目录保存了 Git 状态、暂存/未暂存二进制补丁、非忽略文件 SHA-256 和前端验证副本。未清理或暂存用户改动。测试数据、浏览器截图、构建输出及性能探针均放在临时目录。

证据目录：`/var/folders/fr/k3vpq27d5sd5zsy0ll16r_ym0000gn/T/aln-chart-fixes-tkcmstvg`。

## 修改行为

1. 首次绘图和复位使用有限数值的最小值减 5、最大值加 5。5 是字段原有单位的绝对值，不是百分比。边界从完整当前数据表读取，不依赖抽稀后的散点。空值、非数值和非有限数不参与计算。
2. 右侧配置新增各指标的 Y 轴最小值、最大值及“应用”。只有提交时重绘，输入过程中不反复重绘；拒绝空值、非有限值和最小值不小于最大值。手动范围是按当前数据表隔离的页面状态；复位恢复自动范围。平移/缩放模式的双击复位同样遵循该边界。
3. 选择样式从逐条 `restyle` 改为一次批量 `restyle`。WebGL 门限按整张图的原始点总数计算；中位数线、标签、蜂群密度及源行映射保留。
4. 移除顶部“谐振器 › 数据分析”面包屑；项目和 HTML 报告入口移到右侧配置。图形类型固定在左侧，其余操作单独横向滚动。窄窗口图表标题和工具可换行。
5. 单击绘图区空白处清除工作区选中行和 Plotly 的持久选框。指针抬起由窗口监听，覆盖 Plotly 在图表外放置的拖动遮罩。移动超过点击门限的拖动不触发清空。
6. Plotly 的悬停信息可能在快速点击、WebGL 平移后滞后。先核对实际点击坐标；缓存点不匹配时，使用当前轴投影寻找命中的真实行，避免误选旧点，也避免丢失真实点点击。扫描仅限当前子图，在发生悬停失配时进行。
7. 切换平移/缩放时清理旧选框，保留选中行。忽略 Plotly 程序性清框产生的、没有选择区域的空 `selected` 事件；实际空区域框选仍可清空选择。

## 诊断及分项验证

使用真实 Chromium/Plotly 鼠标拖动。样本：2,016 行、8 指标、14 档、约 16,128 原始散点。

- 修改前生成约 16,240 个 SVG 点，8 次选择刷新累计约 957ms。
- 只合并刷新：约 129ms。
- 再按整图门限启用 WebGL：约 28ms，SVG 点减少到 112 个中位数标记。
- 保持相同 Y 范围和数据，强制 SVG 与 WebGL 做 12 次范围重绘对照：中位数约 90.6ms 与 8.6ms。此指标是范围重绘时间，不代表跨平台帧率保证。
- 原始选框不消失已复现；修复后 `layout.selections` 和选框 DOM 同时清空。
- macOS Electron 快速空白点击连续 5 次通过；另补测平移后的 WebGL 真实点点击。

证据：`baseline.jsonl`、`batched.jsonl`、`batched-gl.jsonl`、`fixed-blank.jsonl`、`render-benchmark.json`、`electron-repeat.log`。

## 回归夹具修正

旧工作表测试在第二次拖动时使用了已经离开屏幕的坐标：目标行约 Y=1259px，而窗口高 868px，实际没有任何 pointerdown/pointermove 事件。增加拖动前 `scrollIntoViewIfNeeded` 并重新读取坐标；工作表业务代码没有修改。

点选测试先等待前一次取消选择的样式完成，再读取点击位置。兼容 Plotly 清除属性时使用 `undefined` 或 `null`。双击复位在平移模式验收；框选模式的双击用于取消选择。

## 最终检查

在隔离前端副本中执行（依赖复用现有项目安装，未全局安装）：

- `npm test`：45 个文件通过、1 个文件跳过；241 项通过、1 项 ONNX 测试因模型环境未配置而跳过。日志 `final-unit.log`。
- `npm run typecheck`：exit 0。日志 `final-typecheck.log`。
- `npm run build`：exit 0，包括 Electron main/preload 编译。日志 `final-build.log`。
- Playwright `chartControls.spec.ts known-gaps.spec.ts swarm.spec.ts plotly.spec.ts`：最终一次运行 31/31 通过，包含真实浏览器和 macOS Electron。日志 `final-verification.log`，截图及 trace 位于 `final-verification/`。
- `git diff --check`：exit 0。
- 比对开始时 791 个非忽略文件的校验值：只改变本轮明确涉及的 9 个已有文件，未发现其他文件变化。结果 `protection-final.json`。
- `npm run knip` 非零：仍报告 9 个其他既有验收 harness 未登记、`uv` 未登记、4 个既有未使用导出及 14 个既有未使用类型。本轮新增 harness 已加入入口，未用其他代码清理来扩展任务范围。日志 `knip-final.log`。

本轮另新增范围控件及其单测、图表交互验收 harness/用例和本记录。

Windows 目标机器及服务器上的实际拖动性能未验收；本机结果不能代替 Windows 验收。此次未修改数据库执行器或压缩包解压流程。
