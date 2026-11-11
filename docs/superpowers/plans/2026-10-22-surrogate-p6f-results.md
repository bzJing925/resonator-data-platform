# P6F：125 条原曲线溯源及校准规则审计

本轮完成原始曲线恢复、raw Qp 重提参一致性和现有校准匹配规则的顺序敏感性检查。未恢复历史去嵌配置；未重算 de 标签，未训练模型。属于回顾性诊断。

## 证据

源文件 `/Users/jingbozuo/00.PROJECTS/ALN-DATA/data/#2.zip` 有 22050 个成员，其中 600 个名称含 OPEN/SHORT 的 s2p；未发现 .py/.json/.txt/.log 配方或运行日志。项目目录中的文件名搜索未找到独立 de.py 历史脚本。文档描述的是移植规则，不是这批 Excel 的运行收据，不能证明实际使用配置。

P6E 的 8 个小面积 PF 结构、125 条记录全部找到精确命名的原始 s2p，并按已有拆端口代码恢复对应 s1p。125 条 raw Qp 与原表一致，最大绝对差 5.8265e-13。其余四目标及频率重提取数值也保留，但本轮一致性结论只针对已逐条比较的 Qp。原始文件与代码 SHA256 留档。

使用当前 match_calibration 对每条 DUT 枚举所有方法，分别按压缩包顺序及逆序提供校准文件名；仅匹配，不执行去嵌：

| 当前规则 | 成功匹配 | 逆序后配对改变 |
|---|---:|---:|
| default | 125/125 | 125/125 |
| original | 125/125 | 0/125 |
| vz | 125/125 | 0/125 |
| gsg100 | 0/125 | 不适用 |
| basic | 0/125 | 不适用 |

default 选择首个可用校准件，顺序敏感是当前实现行为；不说明历史 Excel 使用了 default，也不等于已经证实历史计算错误。original/vz 能匹配也不足以证明其是原配置。校准件在本轮仅做 ZIP 成员清单和名称规则匹配，尚未加载其曲线进行有效性/频率网格检查。

## 判断与交接

raw Qp 提取不一致不是这 125 条记录已观察到的问题；de 处理仍存在来源缺口。当前不覆盖原标签、不扩大模型支持范围。

下一步可以做明确标记的“当前规则去嵌敏感性实验”：核对 original/vz 是否逐器件给出完全相同配对，恢复所需校准件，检查频率、端口及曲线有效性，再比较候选规则生成的 de 指标与旧 Excel。即使数值吻合，也应区分“数值复现”和“历史流程来源已证实”；不得为迎合 Excel 调校准配对。保持 default 顺序两个对照、失败记录和全部差值，不以最接近 Excel 的规则自动改标签。

若需要最终确认历史流程，仍需原运行脚本/配置或操作者记录。新尺寸预测可信度仍需独立实测；仿真 Q 不混入训练。

## 复现与文件

ML worktree backend：`/Users/jingbozuo/Projects/aln-data-backend-ml/backend`。
产物：`surrogate_data/optimization_p6f_20261022/audit/`。

- raw_curve_audit.csv：125 条溯源及 raw Qp 差值。
- calibration_inventory.csv：600 个校准成员及 CRC/大小。
- candidate_calibration_matches.csv：1250 条方法/顺序匹配结果，含失败理由。
- raw_parents、S11、S22：选择性恢复曲线。
- receipt.json：输入及脚本/提参/匹配代码 SHA256。

```bash
uv run --no-sync python scripts/audit_optimization_p6f.py \
 --archive '/Users/jingbozuo/00.PROJECTS/ALN-DATA/data/#2.zip' \
 --p6e-root surrogate_data/optimization_p6e_20261022 \
 --out surrogate_data/p6f_reproduce_new
uv run --no-sync ruff check scripts/audit_optimization_p6f.py
```

验证收据见产物根目录 verification.json。未修改产品算法、原冻结数据或既有 dirty 文件；没有 Git 提交或部署。
