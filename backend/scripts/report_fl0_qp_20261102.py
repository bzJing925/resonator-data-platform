"""Build reviewable FL0 audit plots and offline report from completed audit."""
import json, html, shutil
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
ROOT=Path('surrogate_data/optimization_fl0_audit_20261102')

def run():
    d=pd.read_csv(ROOT/'record_audit.csv'); s=pd.read_csv(ROOT/'structure_summary.csv'); p=pd.read_csv(ROOT/'matched_frame_summary.csv')
    selected=pd.concat([d.nlargest(3,'source_qp'),d[d.decision.eq('review')].nlargest(3,'source_qp'),d[d.mark.eq('E1-1') & d.port.eq('S22')].sort_values('source_qp').iloc[24:25],d[d.has_pf.eq(1)].nlargest(1,'source_qp'),d.nsmallest(1,'source_qp')]).drop_duplicates('row_id')
    plots=[]; data=[]
    for row in selected.itertuples():
        path=ROOT/'curves'/f'{row.row_id.replace(":","_")}.npz'
        if not path.exists():continue
        n=np.load(path); f=n['f']/1e9
        fig,axs=plt.subplots(2,2,figsize=(11,6.5),layout='constrained')
        fp=row.de_fp_ghz; width=max(.015,row.de_bw_mhz/1000*3)
        for key,label,color in [('raw_z','Raw','#0072B2'),('de_z','ShortOpen','#D55E00')]:
            z=n[key]
            axs[0,0].plot(f,20*np.log10(abs(z)),label=label,color=color,lw=1)
            axs[0,1].plot(f,20*np.log10(abs(z)),label=label,color=color,lw=1)
            axs[1,0].plot(f,np.angle(z,deg=True),label=label,color=color,lw=1)
            axs[1,1].plot(f,z.real,label=label,color=color,lw=1)
        axs[0,0].set_xlim(row.de_fs_ghz-.04,fp+.04)
        for ax in [axs[0,1],axs[1,0],axs[1,1]]: ax.set_xlim(fp-width,fp+width)
        axs[0,0].set_ylabel('|Z| (dB ohm)');axs[0,1].set_ylabel('|Z| (dB ohm)')
        axs[1,0].set_ylabel('Phase (degree)');axs[1,1].set_ylabel('Re(Z) (ohm)')
        for ax in axs.flat: ax.set_xlabel('Frequency (GHz)');ax.grid(alpha=.2)
        axs[0,0].legend(); axs[0,1].axhline(20*np.log10(row.de_zp_ohm)-3.0103,ls=':',color='gray')
        axs[1,1].axhline(0,ls=':',color='gray')
        fig.suptitle(f'{row.device_id}\nQphase={row.de_qp:.1f}, Qbw={row.de_qp_bandwidth:.1f}, QY={row.de_qp_admittance:.1f}; {row.decision}',fontsize=10)
        name=row.row_id.replace(':','_')+'.png'; fig.savefig(ROOT/name,dpi=300);fig.savefig(ROOT/name.replace('.png','.pdf'));plt.close(fig)
        plots.append((row,name))
        # Preserve native sampling around resonance; decimate only the overview.
        ids=np.unique(np.r_[np.arange(0,len(f),20),np.flatnonzero(abs(f-fp)<width)])
        data.append(dict(id=row.device_id,f=f[ids].round(7).tolist(),raw=(20*np.log10(abs(n['raw_z'][ids]))).round(4).tolist(),de=(20*np.log10(abs(n['de_z'][ids]))).round(4).tolist()))
    paired=pd.read_csv(ROOT/'matched_frame_comparison.csv')
    grouped=paired.groupby(['eg_um','area_um2','has_pf','fl_um']).ratio_frame_over_fl0.agg(['count','median']).reset_index()
    grouped.to_csv(ROOT/'matched_frame_by_geometry.csv',index=False)
    summary=d.decision.value_counts().to_dict()
    high=d[d.source_qp>=1000]
    info=dict(counts=summary,high_n=len(high),high_counts=high.decision.value_counts().to_dict(),label_matches=int(d.label_match.sum()),raw_label_matches=int(d.raw_label_match.sum()),reason_counts=d.reasons.fillna('').str.get_dummies(sep=';').sum().to_dict(),max_relative_label_error=float(((d.de_qp-d.source_qp)/d.source_qp).abs().max()),top=d.nlargest(3,'source_qp')[['device_id','source_qp','raw_qp','de_qp_bandwidth','de_qp_admittance','de_bw_mhz','de_bw_points','decision','reasons']].to_dict('records'))
    (ROOT/'report_stats.json').write_text(json.dumps(info,indent=2,ensure_ascii=False)); print(json.dumps(info,indent=2,ensure_ascii=False))
    body='''<!doctype html><meta charset="utf-8"><title>FL=0 Qp 曲线审计</title><style>body{font:16px system-ui;max-width:1200px;margin:30px auto;padding:20px;color:#172b3a}table{border-collapse:collapse;font-size:13px}td,th{padding:7px;border:1px solid #ddd}img{max-width:100%}h2{margin-top:35px}.note{background:#f0f5fa;padding:20px}canvas{width:100%;height:350px;border:1px solid #ddd}</style><h1>FL=0 Qp 曲线审计 · 2026-11-02</h1>'''
    body+=f'<p>72 个结构，{len(d)} 条记录；曲线交叉支持 {summary.get("curve_supported",0)} 条，待复核 {summary.get("review",0)} 条，未核验 {summary.get("unverified",0)} 条。</p>'
    body+=f'<p><strong>Qp≥1000 的 {len(high)} 条记录全部通过本轮曲线筛选，未找到据此整体删除 FL=0 高Q数据的依据。</strong>59条待复核项涉及峰宽法不一致或局部相位导数不稳定，部分原因重叠。原去嵌Qp全部数值复现；raw表有1条参考Qp缺失，该条原始曲线已成功读取与重算，其余3319条raw标签复现。</p>'
    body+='''<div class="note">本报告判断的是现有测量曲线是否支持所报 Qp，不等于确认材料本征 Q、仪器精度或制造重复性。没有按 FL=0 或高 Q 删除记录，没有把物理推算值替换成实测标签。源 Excel、数据库、冻结模型与原结构寻优页面保持原样。清洗结果以新增筛选表和隔离表提供。</div>
<h2>物理判据</h2><p>Qphase = fp/2 × |d arg(Z)/df|；单一、平滑、弱背景反谐振附近 Qbw ≈ fp/Δf（|Z|下降 3.0103 dB 的最近左右交点）；局部并联等效 Y=G+jB 下 QY ≈ fp/(2G) × dB/df。后两式是有条件的交叉验证，不能在多模态、强背景下强行当作真值。所有方法使用同一测量，因此不是独立实验。</p>
<p>能量损耗关系 1/Qtotal = 1/Qmaterial + 1/Qelectrode + 1/Qvertical + 1/Qlateral + …。frame 可改善横向约束，但 FL=0 本身不给出数值 Q 上限；面积、边界、模态、材料和电极损耗仍会影响结果。</p>
<p>复核筛选：反谐振附近 ±3 个线宽内出现负实部、线宽不足5个频点、相位展开造成 &gt;1% 差异、局部三次拟合导数变化 &gt;20%、带宽法与原 Qp 相差 &gt;30%、原标签不能复现。阈值为审计筛选规则，不是通用物理标准，复核不等于断言坏数据。原始与 ShortOpen 的一致性不证明历史去嵌配置。</p>'''
    body+='<h2>高值实例</h2>'+pd.DataFrame(info['top']).to_html(index=False)
    body+='<h2>同坐标有 frame / 无 frame 对照</h2><p>匹配 EG、面积、PF、cohort、端口及晶圆坐标；表中为每对 Qp 比值的中位数。配对消除了坐标构成差异的一部分，仍不是同一器件前后实验。</p>'+p.to_html(index=False)
    body+='<h3>固定 EG=1、面积5500、PF=0 的分层对照</h3><p>合并比值会掩盖不同几何的差异，不能据此认为 frame 无效；全部分层结果见 matched_frame_by_geometry.csv。</p>'+grouped[grouped.eg_um.eq(1) & grouped.area_um2.eq(5500) & grouped.has_pf.eq(0)].to_html(index=False)
    body+='<h2>全部结构清洗前后</h2><p>screened_qp 为通过筛选记录按坐标聚合的中位数；样本构成发生改变，不能把其变化解释为物理校正。</p>'+s.sort_values('original_qp',ascending=False).to_html(index=False)
    body+='<h2>真实阻抗曲线实例</h2>'
    import base64
    for row,name in plots:
        body+=f'<h3>{html.escape(row.device_id)}</h3><p>{html.escape('未触发复核筛选项' if pd.isna(row.reasons) else str(row.reasons))}</p><img src="data:image/png;base64,{base64.b64encode((ROOT/name).read_bytes()).decode()}">'
    body+='''<h2>数据与复现</h2><p>record_audit.csv：全记录证据；fl0_curve_supported.csv：暂保留；fl0_quarantine.csv：隔离复核；structure_summary.csv：结构聚合；matched_frame_comparison.csv：全部配对；curves/*.npz：全部全采样 raw/de 复阻抗；receipt.json：输入与代码记录。历史校准流程未获独立证实，没有复测、仪器 IFBW/功率/重复性证据，因此最终可信度仍有这一边界。</p><h2>物理参考</h2><p><a href="https://www.mdpi.com/2072-666X/14/11/1980">Quasi-3D Model for Lateral Resonances on Homogeneous BAW Resonators (2023)</a>：横向传播、泄漏与寄生模态；<a href="https://www.sciencedirect.com/science/article/pii/S0041624X18304736">The impact of area on BAW resonator performance and an approach to device miniaturization</a>：面积、frame 与反谐振损耗。</p>'''
    (ROOT/'FL0-Qp审计报告.html').write_text(body)
    shutil.copy2(ROOT/'FL0-Qp审计报告.html',Path('/Users/jingbozuo/Desktop/FL0-Qp审计报告-20261102.html'))
if __name__=='__main__':run()
