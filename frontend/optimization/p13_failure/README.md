# P13 O15 独立离线失败报告查看器

科学结论保持 **REJECTED**。页面主结论为“当前证据不支持 k² 提升 1 个百分点”；原 GR0 仍为 BLOCKED，GR0-S 仅覆盖 scoped failure delivery。

## 离线使用

直接用浏览器打开：

`backend/surrogate_data/optimization_p13_20261027_r01/viewer/index.html`

HTML 内嵌全部 CSS、JavaScript 和首份数据，不需要 HTTP 服务，不发起外部请求。同目录 `browser_data.json` 与内嵌数据语义完全一致，可用于本地导入。导入会检查固定候选 ID/顺序、完整指标矩阵、null 等级、决策和全部登记来源描述；失败时保留当前有效报告。

页面仅做显示舍入。原始完整精度字符串保留在 JSON 中，候选完整字段与指标来源详情也可在页面展开。P12 的 k²/Qp 是历史模型预测；C19 的 P3_STRUCTURES Qs/BodeQ 是同结构实测提参值，两者不合并为当前推荐。

## 所有文件与源码

- 源码：`frontend/optimization/p13_failure/src/`
- 构建与验证：`frontend/optimization/p13_failure/scripts/`
- 静态/构建/校验测试：`frontend/optimization/p13_failure/tests/`
- T18 Playwright 配置与用例：`frontend/optimization/p13_failure/playwright.config.ts`、`tests/browser/viewer.spec.ts`
- 输出与日志：`backend/surrogate_data/optimization_p13_20261027_r01/viewer/`

## 命令（从本目录执行）

```bash
../../node_modules/.bin/tsc -p tsconfig.json
node scripts/build.mjs
node scripts/build.mjs --check
node --test .build/tests/source-contract.test.js
node --test .build/tests/build-contract.test.js
node --test .build/tests/static-contract.test.js
node --test .build/tests/validation.test.js
node scripts/verify.mjs
node scripts/finalize.mjs
```

不安装或同步依赖，不修改任何 package lock。构建只读取冻结 O14 与 T15 清单，复制完整精度值并核对哈希，不执行预测、训练、分级、寻优、COMSOL 或重采样。

## T18 边界

Playwright specs/config 仅为后续 T18 准备。本任务的通过范围仅是 strict TypeScript、构建可复现性、源码契约、静态数据契约和导入校验单元测试；**不构成 T18 浏览器验收**。
