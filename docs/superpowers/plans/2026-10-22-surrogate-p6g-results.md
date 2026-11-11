# P6G：候选去嵌规则敏感性对照

2026-10-22。完成 125 条记录 × 3 套配对的 375 条曲线计算。结论：当前 original/vz 配对数值复现了旧表的 Qp、Qs、k²、fs/fp；小面积 PF 的 Qp 模型区间仍 0/8 覆盖。历史运行配置来源仍未证实，不把数值吻合等同于流程来源证明。

## 方法与边界

直接使用 P6F 已保存配对清单，计算 original 正序、default 正序、default 逆序。先验证 original 与 vz 在 125 条记录上的 OPEN/SHORT 对完全相同，故合并计算。没有搜索最接近 Excel 的校准件，没有修改匹配算法或标签，没有重新训练/校准模型。

从 #2.zip 选择性恢复实际使用的校准父文件并拆端口，使用当前 ShortOpen 与当前提参代码。375 条组合均满足 DUT/OPEN/SHORT 频率网格完全一致、参考阻抗一致、输入及输出 S 参数有限；提参均成功，无捕获警告。这些数值检查不代表全部物理有效性，例如本轮没有完成校准件非理想效应或被动性诊断。

## 与旧 de Excel 的差值

| 配对 | k² MAE（百分点） | Qs MAE | Qp MAE | Bode Q MAE |
|---|---:|---:|---:|---:|
| original/vz | 9.24e-17 | 3.33e-12 | 4.83e-12 | 0.002842 |
| default 正序 | 0.01787 | 49.113 | 11.877 | 31.032 |
| default 逆序 | 0.01922 | 83.675 | 14.146 | 60.519 |

original/vz 的 Qp 最大绝对差 2.7285e-11；Qs 最大差 1.4097e-11；fs/fp 全部完全一致。按 rtol=atol=1e-8 比较，Qp/Qs/k²/fs/fp 各 125/125 一致。Bode Q 仅 2/125 在该阈值内一致，最大绝对差 0.01218243，最大相对差约 1.215e-5；差异来源尚未定位，不能宣称四目标全部精确复现。

将 original/vz 的 Qp 按原坐标中位数规则聚合，8 个结构的中位数均复现旧 P3 标签，仍全部高于冻结 P6C 区间上限。本轮目标结构每坐标仅一条记录，因此跨行中位数等于跨坐标中位数。

## 判断

这组样本的旧 Qp 可由现有原曲线和位置匹配规则重现，因而不能再以“Qp 去嵌数值不可复现”解释已观察到的模型偏差。但这不证明物理校准完全正确、也不证明历史一定使用了某个脚本；多个相同配对的方法在此数据上不可区分。

default 文件顺序对照明确产生了不同指标；这些替代指标不应回填旧 de 标签。现有实测和仿真仍分开，旧模型、划分和基线均保留。

## 后续交接

优先返回小面积 PF 的结构/位置关系：在固定坐标交集上比较 700 与 900 μm² 的 Qp，明确 FL、端口、设计组及缺失坐标，区分坐标构成差异与面积趋势；仅作配对描述，不将单批器件比较称为面积因果效应。不继续按已查看 test 调权重。Bode Q 微小复现差异可单独追踪配置/拟合实现，不能混同 Qp 失败。

新的复杂模型应使用训练内部验证并保留所有失败对照；所有当前测试均已查看，不能叫新盲测。新尺寸外推仍需独立实测。

## 文件、复现与验证

ML 工作目录 `/Users/jingbozuo/Projects/aln-data-backend-ml/backend`；产物 `surrogate_data/optimization_p6g_20261022/`。audit 下保存校准父文件、拆端口文件、375 条去嵌曲线、candidate_results.csv、summary.csv、calibration_sources.csv、receipt.json。根目录保存结构聚合复核与 verification.json。

```bash
uv run --no-sync python scripts/audit_optimization_p6g.py \
 --archive '/Users/jingbozuo/00.PROJECTS/ALN-DATA/data/#2.zip' \
 --p6f surrogate_data/optimization_p6f_20261022 \
 --p6e surrogate_data/optimization_p6e_20261022 \
 --out surrogate_data/p6g_reproduce_new
uv run --no-sync ruff check scripts/audit_optimization_p6g.py
```

验证：375 条数值前置/输出检查、8 个聚合标签及覆盖独立复核；每种配对选一条曲线，独立直接调用 ShortOpen，与保存曲线复数 S 参数对照通过。未进行第二次完整 375 条重跑，不宣称全量双跑。P6F 762 个交付文件校验不变。未修改产品算法、标签、原模型或既有 dirty 文件；无部署、无提交。
