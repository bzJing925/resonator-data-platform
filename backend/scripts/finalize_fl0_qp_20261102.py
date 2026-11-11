"""Package additive cleaning decisions, evidence manifest, and short Chinese summary."""
from pathlib import Path
import hashlib,json,shutil
import numpy as np,pandas as pd
R=Path('surrogate_data/optimization_fl0_audit_20261102')
d=pd.read_csv(R/'record_audit.csv'); stats=json.loads((R/'report_stats.json').read_text())
out=d[['row_id','device_id','structure_id','mark','port','coord','eg_um','fl_um','area_um2','has_pf','source_qp','decision','reasons']].copy()
out['qp_for_optimization']=out.source_qp.where(out.decision.eq('curve_supported'))
out['valid_qp_for_optimization']=out.decision.eq('curve_supported')
out.to_csv(R/'fl0_qp_cleaning_overlay.csv',index=False)
# Freeze row-level cleaning decisions alongside original values; all other targets unchanged.
records=pd.read_csv('surrogate_data/optimization_p3_20261015/data/records.csv.gz')
records=records.merge(out[['row_id','decision','reasons','valid_qp_for_optimization']],on='row_id',how='left',validate='one_to_one')
records['valid_qp_for_optimization']=records.valid_qp & records.decision.fillna('not_in_fl0_audit').ne('review') & records.decision.fillna('not_in_fl0_audit').ne('unverified')
records['qp_for_optimization']=records.qp.where(records.valid_qp_for_optimization)
records.to_csv(R/'records_with_fl0_qp_screen.csv.gz',index=False)
for name in ['audit_fl0_qp_20261102.py','report_fl0_qp_20261102.py','verify_fl0_qp_20261102.py','finalize_fl0_qp_20261102.py']:
 shutil.copy2(Path('scripts')/name,R/name)
text=f'''# FL=0 Qp 曲线审计与非破坏性清洗

日期：2026-11-02。

覆盖：结构寻优 P3 实测源表中 geometry_eligible 的 FL=0 去嵌数据，72 个结构、3320 条记录。不是所有未知/不合格几何记录，也不是外推候选。

结果：{json.dumps(stats['counts'],ensure_ascii=False)}。Qp≥1000 共 {stats['high_n']} 条，其中 {json.dumps(stats['high_counts'],ensure_ascii=False)}。原 Qp 标签复现 {stats['label_matches']}/3320；raw 标签复现 {stats['raw_label_matches']}/3320。

raw表中1条参考Qp缺失，非重算冲突；该条原始曲线成功读取与重算，其余3319条raw标签复现。

曲线支持表示原 Qp 得到局部相位、线宽与无源性筛选支持；review 表示隔离待复核，不等于证实错误。不能仅以 FL=0 或数值偏高删除。规则与阈值在 protocol.json；无独立复测，无法确认仪器系统误差、校准不确定度或本征 Q。ShortOpen original 配方数值复现也不等于历史配置已被证实。

## 交付

- FL0-Qp审计报告.html：离线报告，含代表性实际 raw/de 曲线与全部结构清洗前后统计。
- record_audit.csv：全部3320条审计证据及理由。
- fl0_curve_supported.csv / fl0_quarantine.csv：暂保留/隔离复核分区。
- fl0_qp_cleaning_overlay.csv：原值 source_qp 保留；qp_for_optimization 对待复核记录留空，可按 row_id 应用。
- records_with_fl0_qp_screen.csv.gz：全记录加审计字段，不修改原 qp 和 valid_qp；使用新的 qp_for_optimization / valid_qp_for_optimization 才会应用筛选。
- structure_summary.csv：按坐标中位数重算结构级 Qp；筛选造成的样本组成变化不是物理修正。
- curves/*.npz：全部已核验记录的全采样频率、raw_z、de_z（复数阻抗），单位 Hz/ohm。
- matched_frame_comparison.csv：匹配 EG/面积/PF/cohort/端口/坐标后的有无 frame 对照。
- validation.json：解析RLC阳性与负电阻反例验证、完整分区与曲线数量校验。

源 Excel、原 ZIP、数据库、模型与原寻优页面未改动。没有为了得到预期趋势而降低 Qp，也没有重训模型。清洗后的新中位数仅供敏感性复核；隔离记录应在复测或更完整的多模态/校准分析后再决定是否永久排除。

## 复现

在 /Users/jingbozuo/Projects/aln-data-backend-ml/backend 下运行：

    uv run python scripts/audit_fl0_qp_20261102.py
    uv run python scripts/report_fl0_qp_20261102.py
    uv run python scripts/verify_fl0_qp_20261102.py
    uv run python scripts/finalize_fl0_qp_20261102.py

审计脚本拒绝覆盖已有输出；重跑时将 ROOT 设为新的独立目录，并同步其余脚本路径。无源输入修改，无全局依赖安装。审计公式已用 Q=500/1000/1700 的解析并联 RLC 验证，四种结果误差均小于0.5%；这验证代码实现，不验证实验仪器。

物理参考：Quasi-3D Model for Lateral Resonances on Homogeneous BAW Resonators, 2023, https://www.mdpi.com/2072-666X/14/11/1980 。1/Qtotal 是多种损耗通道的和，FL=0 不产生固定数值上限；Qphase、Qbw、QY 的比较前提及公式详见 HTML。
'''
(R/'README.md').write_text(text)
manifest={str(p.relative_to(R)):hashlib.sha256(p.read_bytes()).hexdigest() for p in R.rglob('*') if p.is_file() and p.name!='sha256_manifest.json'}
(R/'sha256_manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False))
print('Packaged',len(manifest),'files; preserved original Qp values.')
